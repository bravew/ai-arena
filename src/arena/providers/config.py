"""Provider configuration loading and validation."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ENV_REFERENCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_TEMPLATE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
# A header value may carry an auth scheme in front of the reference: `Bearer ${TOKEN}`.
ENV_HEADER_TEMPLATE = re.compile(r"^(?:(?:Bearer|Basic|Token)\s+)?\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
SECRET_FIELD = re.compile(r"(api.?key|authorization|auth|secret|token|password|credential)", re.I)


class ConfigLoader(yaml.SafeLoader):
    """Safe loader that preserves ISO dates as strings for version fields."""


def _construct_yaml_timestamp(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> str:
    return loader.construct_scalar(node)


def validate_credential_endpoint(endpoint: str) -> str:
    """Return a safe endpoint for a credential-bearing request.

    The endpoint must be absolute HTTPS, with a host and no URL userinfo. A
    loopback host may use plain HTTP, because the DEV_PLAN keeps the gateway and
    the Docker bridge on loopback; any other host must use HTTPS. A port of `0` is
    accepted as a placeholder. The check runs before any credential is loaded or
    sent.
    """
    message = "credential-bearing provider endpoints require an absolute HTTPS URL"
    try:
        parts = urlsplit(endpoint)
        _ = parts.port  # a non-numeric or out-of-range port raises ValueError
        hostname = parts.hostname
    except ValueError as error:
        raise ValueError(message) from error
    if (
        not hostname
        or any(character.isspace() or ord(character) < 33 for character in hostname)
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError(message)
    scheme = parts.scheme.lower()
    if scheme == "https":
        return endpoint
    if scheme == "http" and _is_loopback_host(hostname):
        return endpoint
    raise ValueError(message)


def _is_loopback_host(hostname: str) -> bool:
    """Whether a URL host names this machine (localhost, 127.0.0.0/8, ::1)."""
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


ConfigLoader.add_constructor("tag:yaml.org,2002:timestamp", _construct_yaml_timestamp)


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProviderKey(ConfigModel):
    id: str
    env: str

    @model_validator(mode="after")
    def env_must_be_name(self) -> ProviderKey:
        if not ENV_REFERENCE.fullmatch(self.env) and not ENV_TEMPLATE.fullmatch(self.env):
            raise ValueError("must be an environment variable reference, not a secret value")
        return self


SubscriptionRuntime = Literal["gateway", "local-cli"]
# Vendors whose own CLI contract is recorded in docs/subsystems/provider-runtimes.md.
LOCAL_CLI_VENDORS = frozenset({"claude", "chatgpt"})


class Subscription(ConfigModel):
    """A subscription-backed provider.

    `runtime` says who talks to the vendor: `gateway` (the daemon holds the imported session
    and signs requests) or `local-cli` (the vendor's own CLI runs on its own sign-in). Leaving
    it out keeps the existing `gateway` behavior.
    """

    vendor: Literal["claude", "chatgpt", "copilot"]
    plan: str
    import_: str = Field(alias="import")
    runtime: SubscriptionRuntime = "gateway"

    @model_validator(mode="after")
    def runtime_matches_vendor(self) -> Subscription:
        if self.runtime == "local-cli" and self.vendor not in LOCAL_CLI_VENDORS:
            raise ValueError(f"runtime local-cli has no CLI contract for vendor {self.vendor}")
        return self


class Provider(ConfigModel):
    id: str
    kind: Literal["mock", "cassette"] | None = None
    enabled: bool = True
    keys: list[ProviderKey] = []
    subscription: Subscription | None = None

    @model_validator(mode="after")
    def runtime_belongs_to_subscription(self) -> Provider:
        if "runtime" in (self.model_extra or {}):
            raise ValueError("runtime belongs under subscription, not on the provider")
        return self

    @model_validator(mode="after")
    def credential_endpoints_require_https(self) -> Provider:
        raw_apis = (self.model_extra or {}).get("apis", {})
        if (self.keys or self.subscription is not None) and isinstance(raw_apis, dict):
            for endpoint in cast(dict[str, Any], raw_apis).values():
                if isinstance(endpoint, str):
                    validate_credential_endpoint(endpoint)
        return self

    @model_validator(mode="after")
    def has_auth_or_builtin_kind(self) -> Provider:
        if self.subscription is not None and self.keys:
            raise ValueError("use keys or subscription, not both")
        if self.kind is None and not self.keys and self.subscription is None:
            raise ValueError("provider requires keys or a subscription")
        if self.kind is not None and (self.keys or self.subscription is not None):
            raise ValueError("mock and cassette providers cannot define keys or subscription")
        return self


class ProviderConfig(ConfigModel):
    providers: list[Provider]

    @model_validator(mode="after")
    def unique_ids(self) -> ProviderConfig:
        ids = [provider.id for provider in self.providers]
        if len(ids) != len(set(ids)):
            raise ValueError("provider ids must be unique")
        return self


def parse_yaml(path: Path) -> Any:
    """Load one YAML document; malformed or empty files are errors."""
    with path.open(encoding="utf-8") as stream:
        data = yaml.load(stream, Loader=ConfigLoader)
    if data is None:
        raise ValueError("file is empty")
    return data


def load_providers(path: Path) -> ProviderConfig:
    """Parse and validate a provider configuration file."""
    return ProviderConfig.model_validate(parse_yaml(path))


def format_validation_error(path: Path, error: ValidationError) -> list[str]:
    """Format Pydantic errors with their source file and YAML path."""
    issues: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "<root>"
        issues.append(f"{path}:{location}: {item['msg']}")
    return issues


def reject_plaintext_secrets(value: Any, path: Path, location: str = "") -> list[str]:
    """Reject common secret-bearing fields anywhere in YAML config data."""
    issues: list[str] = []
    if isinstance(value, dict):
        mapping = cast(Mapping[Any, Any], value)
        for key, child in mapping.items():
            child_location = f"{location}.{key}" if location else str(key)
            has_secret_name = isinstance(key, str) and SECRET_FIELD.search(key)
            if has_secret_name and child is not None:
                child_mapping = cast(dict[str, Any], child) if isinstance(child, dict) else {}
                env_value = child_mapping.get("env")
                is_env_reference = (
                    isinstance(child, str) and ENV_HEADER_TEMPLATE.fullmatch(child) is not None
                ) or (
                    set(child_mapping) <= {"env"}
                    and isinstance(env_value, str)
                    and ENV_REFERENCE.fullmatch(env_value) is not None
                )
                if not is_env_reference:
                    issues.append(
                        f"{path}:{child_location}: secrets must be environment references"
                    )
            issues.extend(reject_plaintext_secrets(child, path, child_location))
    elif isinstance(value, list):
        items = cast(list[Any], value)
        for index, child in enumerate(items):
            issues.extend(reject_plaintext_secrets(child, path, f"{location}[{index}]"))
    return issues

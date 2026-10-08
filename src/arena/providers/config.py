"""Provider configuration loading and validation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ENV_REFERENCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_TEMPLATE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
SECRET_FIELD = re.compile(r"(api.?key|authorization|auth|secret|token|password|credential)", re.I)


class ConfigLoader(yaml.SafeLoader):
    """Safe loader that preserves ISO dates as strings for version fields."""


def _construct_yaml_timestamp(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> str:
    return loader.construct_scalar(node)


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


class Subscription(ConfigModel):
    vendor: Literal["claude", "chatgpt", "copilot"]
    plan: str
    import_: str = Field(alias="import")


class Provider(ConfigModel):
    id: str
    kind: Literal["mock", "cassette"] | None = None
    enabled: bool = True
    keys: list[ProviderKey] = []
    subscription: Subscription | None = None

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
                    isinstance(child, str) and ENV_TEMPLATE.fullmatch(child) is not None
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

"""List the models a provider offers, and propose catalog entries for them.

A subscription provider asks its vendor CLI. An API provider asks its own model-list endpoint
with the provider's key. Neither path sends a model request.

A proposal never carries a price. A paid model has no verified price until someone adds one by
hand with its source, and the catalog does not accept a paid model without a price, so only a
subscription entry (`pricing: subscription`) can be written to the catalog file. API
proposals are printed as YAML with the price left as a comment to fill in.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx

from arena.catalog.config import load_catalog
from arena.providers.config import Provider, validate_credential_endpoint
from arena.providers.runtimes import (
    Probe,
    ProtocolFailure,
    read_codex_models,
    run_probe,
    spec_for_vendor,
)
from arena.providers.runtimes.detect import RuntimeStatus, detect_runtime

# Which model-list route answers for which protocol, tried in this order.
_PREFERRED = ("responses", "chat", "anthropic")


class DiscoveryError(Exception):
    """Models could not be listed. The message says why and never holds a secret."""


@dataclass(frozen=True)
class DiscoveredModel:
    id: str
    source: str


@dataclass(frozen=True)
class Proposal:
    ref: str
    protocols: tuple[str, ...]
    subscription: bool


def discover_models(
    provider: Provider,
    environ: Mapping[str, str] | None = None,
    *,
    probe: Probe = run_probe,
    client: httpx.Client | None = None,
) -> list[DiscoveredModel]:
    source = os.environ if environ is None else environ
    if provider.subscription is not None:
        return _cli_models(provider, source, probe)
    if provider.kind is not None:
        raise DiscoveryError(f"{provider.id} is a built-in {provider.kind} provider")
    return _api_models(provider, source, client)


def _cli_models(
    provider: Provider, environ: Mapping[str, str], probe: Probe
) -> list[DiscoveredModel]:
    subscription = provider.subscription
    assert subscription is not None
    if subscription.runtime != "local-cli":
        raise DiscoveryError(
            f"{provider.id} uses the gateway runtime, which has no model list; "
            "name its models in the catalog by hand"
        )
    spec = spec_for_vendor(subscription.vendor)
    if spec is None:
        raise DiscoveryError(f"no CLI contract for vendor {subscription.vendor}")
    if spec.runtime != "codex":
        raise DiscoveryError(
            f"the {spec.binary} CLI has no model list command; name its models in the catalog "
            "by hand"
        )
    report = detect_runtime(spec, environ, probe=probe)
    if report.status is not RuntimeStatus.OK or report.binary is None:
        raise DiscoveryError(f"{spec.binary}: {report.status} ({report.detail})")
    try:
        slugs = read_codex_models(report.binary, environ, probe=probe)
    except ProtocolFailure as error:
        raise DiscoveryError(str(error)) from error
    return [DiscoveredModel(slug, f"cli:{spec.binary}") for slug in slugs]


def _api_models(
    provider: Provider, environ: Mapping[str, str], client: httpx.Client | None
) -> list[DiscoveredModel]:
    apis = _apis(provider)
    protocol = next((name for name in _PREFERRED if name in apis), None)
    if protocol is None:
        raise DiscoveryError(f"{provider.id} offers no protocol with a model list")
    if not provider.keys:
        raise DiscoveryError(f"{provider.id} has no key to list models with")
    try:
        base = validate_credential_endpoint(apis[protocol]).rstrip("/")
    except ValueError as error:
        raise DiscoveryError(
            "credential-bearing provider endpoints require an absolute HTTPS URL"
        ) from error
    variable = provider.keys[0].env
    variable = variable[2:-1] if variable.startswith("${") and variable.endswith("}") else variable
    secret = environ.get(variable, "")
    if not secret:
        raise DiscoveryError(f"environment variable {variable} is unset")
    if protocol == "anthropic":
        url = f"{base}/v1/models"
        headers = {"x-api-key": secret, "anthropic-version": "2023-06-01"}
        params: dict[str, Any] = {"limit": 1000}
    else:
        url = f"{base}/models"
        headers = {"Authorization": f"Bearer {secret}"}
        params = {}
    owned = client is None
    http = client or httpx.Client(timeout=15.0)
    try:
        response = http.get(url, headers=headers, params=params, follow_redirects=False)
    except httpx.HTTPError as error:
        raise DiscoveryError(f"{provider.id}: {type(error).__name__}") from error
    finally:
        if owned:
            http.close()
    if not 200 <= response.status_code < 300:
        raise DiscoveryError(f"{provider.id}: model list answered HTTP {response.status_code}")
    try:
        body: Any = response.json()
    except ValueError as error:
        raise DiscoveryError(f"{provider.id}: model list is not JSON") from error
    data: Any = cast(dict[str, Any], body).get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise DiscoveryError(f"{provider.id}: model list has no data array")
    ids = [
        str(cast(dict[str, Any], item)["id"])
        for item in cast(list[Any], data)
        if isinstance(item, dict) and "id" in item
    ]
    return [DiscoveredModel(model_id, f"api:{protocol}") for model_id in sorted(set(ids))]


def _apis(provider: Provider) -> dict[str, str]:
    raw: Any = (provider.model_extra or {}).get("apis", {})
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in cast(dict[Any, Any], raw).items() if isinstance(v, str)}


def propose(
    provider: Provider, models: list[DiscoveredModel], catalog_path: Path | None = None
) -> list[Proposal]:
    """Catalog entries for models the catalog does not name yet."""
    known: set[str] = set()
    if catalog_path is not None and catalog_path.exists():
        known = {item.ref for item in load_catalog(catalog_path).models}
    subscription = provider.subscription is not None
    protocols = ("responses",) if subscription else tuple(_apis(provider))
    return [
        Proposal(f"{provider.id}/{model.id}", protocols, subscription)
        for model in models
        if f"{provider.id}/{model.id}" not in known
    ]


def render(proposals: list[Proposal]) -> str:
    """YAML list items for the catalog. A paid model gets a price comment, never a number."""
    lines: list[str] = []
    for item in proposals:
        lines.append(f"  - ref: {item.ref}")
        lines.append(f"    protocols: [{', '.join(item.protocols)}]")
        if item.subscription:
            lines.append("    pricing: subscription")
        else:
            lines.append("    # price_per_mtok: {in: ?, out: ?}  # add a verified price")
            lines.append("    # source: <URL of the vendor's price page>")
    return "\n".join(lines) + "\n" if lines else ""


def append_subscription_entries(path: Path, proposals: list[Proposal]) -> list[Proposal]:
    """Append the subscription proposals to the catalog file and return what was written.

    The file is restored if it no longer validates, so a bad append cannot leave a broken
    catalog behind. Paid proposals are skipped because they cannot be valid without a price.
    """
    writable = [item for item in proposals if item.subscription]
    if not writable:
        return []
    original = path.read_text(encoding="utf-8")
    separator = "" if original.endswith("\n") else "\n"
    path.write_text(original + separator + render(writable), encoding="utf-8")
    try:
        load_catalog(path)
    except Exception:
        path.write_text(original, encoding="utf-8")
        raise
    return writable

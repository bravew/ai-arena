"""Turn the model name in a request into a provider and a catalog entry.

Three refusals, each with its own 404 message, so a caller can tell a typo from a switched-off
provider from a model the provider can't serve (DEV_PLAN §5.4 step 3):

- `unknown_model`: the provider isn't in `providers.yaml`, or the model isn't in the catalog;
- `provider_off`: the provider is in the file but switched off. This is checked before the
  catalog, so a model of a disabled provider says "provider off", not "unknown model";
- `not_served`: the model is in the catalog, but the provider has no endpoint for any protocol
  the model speaks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from arena.catalog.config import CatalogModel, ModelCatalog
from arena.core.modelref import ModelRef
from arena.providers.config import Provider, ProviderConfig


class ResolveFailure(StrEnum):
    UNKNOWN_MODEL = "unknown_model"
    PROVIDER_OFF = "provider_off"
    NOT_SERVED = "not_served"


class ResolveError(Exception):
    """A model that can't be used. `message` is what the caller is told."""

    def __init__(self, failure: ResolveFailure, message: str) -> None:
        super().__init__(message)
        self.failure = failure
        self.message = message


@dataclass(frozen=True, slots=True)
class Target:
    """A model the gateway will serve: who asked for it, from whom, and at what effort."""

    ref: ModelRef
    provider: Provider
    model: CatalogModel

    @property
    def asked(self) -> str:
        """The model as the caller named it, effort included."""
        return str(self.ref)

    @property
    def catalog_ref(self) -> str:
        """The catalog's name for it: `provider/model`, no effort."""
        return f"{self.ref.provider}/{self.ref.model}"


def resolve_target(text: str, providers: ProviderConfig, catalog: ModelCatalog) -> Target:
    """The `Target` for the model name `text` (`provider/model[:effort]`), or `ResolveError`."""
    try:
        ref = ModelRef.parse(text)
    except ValueError:
        raise ResolveError(
            ResolveFailure.UNKNOWN_MODEL,
            f"unknown model {text!r}: name it as provider/model, for example "
            "anthropic/claude-opus-5-5",
        ) from None

    provider = next((item for item in providers.providers if item.id == ref.provider), None)
    if provider is None:
        raise ResolveError(
            ResolveFailure.UNKNOWN_MODEL,
            f"unknown model {text!r}: provider {ref.provider!r} is not in providers.yaml",
        )
    if not provider.enabled:
        raise ResolveError(
            ResolveFailure.PROVIDER_OFF,
            f"provider off: {ref.provider!r} is switched off in providers.yaml, "
            f"so {text!r} can't be used",
        )

    catalog_ref = f"{ref.provider}/{ref.model}"
    model = next((item for item in catalog.models if item.ref == catalog_ref), None)
    if model is None:
        raise ResolveError(
            ResolveFailure.UNKNOWN_MODEL,
            f"unknown model {text!r}: {catalog_ref!r} is not in the model catalog",
        )

    reason = _not_served_reason(provider, model)
    if reason is not None:
        raise ResolveError(
            ResolveFailure.NOT_SERVED,
            f"model not served: provider {ref.provider!r} cannot serve {catalog_ref!r}: {reason}",
        )
    return Target(ref=ref, provider=provider, model=model)


def _not_served_reason(provider: Provider, model: CatalogModel) -> str | None:
    """Why `provider` can't serve `model`, or None when it can."""
    extra: dict[str, Any] = provider.model_extra or {}
    apis = extra.get("apis")
    protocols = (model.model_extra or {}).get("protocols")
    if isinstance(apis, dict) and isinstance(protocols, list):
        api_map = cast(dict[str, Any], apis)
        protocol_list = cast(list[Any], protocols)
        offered = {str(name) for name in api_map}
        wanted = [str(name) for name in protocol_list]
        if offered and wanted and not offered.intersection(wanted):
            return (
                f"the model speaks {', '.join(wanted)} and the provider offers "
                f"{', '.join(sorted(offered))}"
            )
    return None


def listed_models(providers: ProviderConfig, catalog: ModelCatalog) -> list[CatalogModel]:
    """The catalog models a caller may ask for: those of an enabled provider that serves them."""
    enabled = {item.id: item for item in providers.providers if item.enabled}
    listed: list[CatalogModel] = []
    for model in catalog.models:
        provider = enabled.get(model.ref.partition("/")[0])
        if provider is not None and _not_served_reason(provider, model) is None:
            listed.append(model)
    return listed

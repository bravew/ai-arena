"""Model catalog loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arena.core.modelref import ModelRef
from arena.providers.config import ProviderConfig, parse_yaml


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    ref: str
    pricing: Literal["subscription"] | None = None
    price_per_mtok: dict[str, float] | None = None

    @model_validator(mode="after")
    def exactly_one_pricing_mode(self) -> CatalogModel:
        if (self.pricing == "subscription") == (self.price_per_mtok is not None):
            raise ValueError("set either price_per_mtok or pricing: subscription")
        if self.price_per_mtok is not None and any(
            price < 0 for price in self.price_per_mtok.values()
        ):
            raise ValueError("prices must be non-negative")
        ModelRef.parse(self.ref)
        return self


class ModelCatalog(BaseModel):
    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)

    price_version: str
    models: list[CatalogModel] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_models(self) -> ModelCatalog:
        refs = [model.ref for model in self.models]
        if len(refs) != len(set(refs)):
            raise ValueError("model refs must be unique")
        return self


class ResolvedModel(BaseModel):
    model: CatalogModel
    provider_enabled: bool


def load_catalog(path: Path) -> ModelCatalog:
    """Parse and validate a model catalog YAML file."""
    return ModelCatalog.model_validate(parse_yaml(path))


def resolve_model(
    model_ref: str, providers: ProviderConfig, catalog: ModelCatalog
) -> ResolvedModel:
    """Resolve a model, checking disabled providers before reporting unknown models."""
    provider_id = ModelRef.parse(model_ref).provider
    provider = next((item for item in providers.providers if item.id == provider_id), None)
    if provider is None:
        raise ValueError(f"unknown provider: {provider_id}")
    if not provider.enabled:
        raise ValueError(f"provider off: {provider_id}")
    model = next((item for item in catalog.models if item.ref == model_ref), None)
    if model is None:
        raise ValueError(f"unknown model: {model_ref}")
    return ResolvedModel(model=model, provider_enabled=provider.enabled)

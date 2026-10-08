from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.catalog.config import load_catalog, resolve_model
from arena.cli import app
from arena.providers.config import load_providers


def test_provider_keys_accept_environment_references(tmp_path: Path) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text(
        "providers:\n  - id: openai\n    apis: {chat: https://api.openai.com/v1}\n"
        "    keys: [{id: main, env: OPENAI_API_KEY}]\n",
        encoding="utf-8",
    )
    assert load_providers(config).providers[0].keys[0].env == "OPENAI_API_KEY"


def test_secret_fields_accept_environment_references(tmp_path: Path) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text(
        "providers:\n  - id: custom\n    apis: {chat: https://example.test}\n"
        "    headers: {Authorization: '${ARENA_TOKEN}'}\n"
        "    keys: [{id: main, env: OPENAI_API_KEY}]\n",
        encoding="utf-8",
    )
    assert load_providers(config).providers[0].id == "custom"


@pytest.mark.parametrize("env", ["sk-plain-secret", "NOT A SECRET"])
def test_provider_rejects_non_environment_names(tmp_path: Path, env: str) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text(
        f"providers:\n  - id: openai\n    keys:\n      - id: main\n        env: {env}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_providers(config)


def test_subscription_provider_is_valid_without_keys(tmp_path: Path) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text(
        "providers:\n  - id: anthropic-max\n    keys: []\n    subscription:\n"
        "      vendor: claude\n      plan: max\n      import: own-sign-in\n",
        encoding="utf-8",
    )
    assert load_providers(config).providers[0].subscription is not None


def test_catalog_accepts_subscription_pricing_with_unknown_price(tmp_path: Path) -> None:
    config = tmp_path / "models.yaml"
    config.write_text(
        "price_version: '2026-10-01'\nmodels:\n"
        "  - ref: anthropic/claude-opus-5-5-sub\n    protocols: [anthropic]\n"
        "    pricing: subscription\n",
        encoding="utf-8",
    )
    catalog = load_catalog(config)
    assert catalog.models[0].pricing == "subscription"
    assert catalog.models[0].price_per_mtok is None


def test_catalog_accepts_token_pricing(tmp_path: Path) -> None:
    config = tmp_path / "models.yaml"
    config.write_text(
        "price_version: '2026-10-01'\nmodels:\n"
        "  - ref: anthropic/claude-opus-5-5\n    protocols: [anthropic]\n"
        "    price_per_mtok: {in: 15, out: 75}\n",
        encoding="utf-8",
    )
    assert load_catalog(config).models[0].price_per_mtok == {"in": 15, "out": 75}


def test_disabled_provider_is_not_unknown_model(tmp_path: Path) -> None:
    providers = tmp_path / "providers.yaml"
    providers.write_text(
        "providers:\n  - id: anthropic\n    enabled: false\n"
        "    keys: [{id: main, env: ANTHROPIC_API_KEY}]\n",
        encoding="utf-8",
    )
    catalog = tmp_path / "models.yaml"
    catalog.write_text(
        "price_version: test\nmodels:\n"
        "  - ref: anthropic/claude-sonnet\n    protocols: [anthropic]\n"
        "    price_per_mtok: {in: 1, out: 2}\n",
        encoding="utf-8",
    )
    provider_config = load_providers(providers)
    model_catalog = load_catalog(catalog)
    with pytest.raises(ValueError, match="provider off: anthropic"):
        resolve_model("anthropic/unknown-model", provider_config, model_catalog)
    result = CliRunner().invoke(app, ["validate", str(providers), str(catalog)])
    assert result.exit_code == 0


def test_validate_reports_all_files_and_paths(tmp_path: Path) -> None:
    providers = tmp_path / "providers.yaml"
    providers.write_text(
        "providers:\n  - id: openai\n    keys:\n      - id: main\n        env: sk-plain-secret\n",
        encoding="utf-8",
    )
    catalog = tmp_path / "models.yaml"
    catalog.write_text(
        "price_version: test\nmodels:\n  - ref: not-a-model\n    pricing: subscription\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["validate", str(providers), str(catalog)])
    assert result.exit_code != 0
    assert str(providers) in result.output
    assert f"{providers}:providers.0.keys.0" in result.output
    assert str(catalog) in result.output

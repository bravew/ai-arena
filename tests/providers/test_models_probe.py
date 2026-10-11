"""Model discovery with fake CLIs and a fake model-list endpoint. No model request is made."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from arena.catalog.config import load_catalog
from arena.cli import app
from arena.providers.config import Provider, load_providers
from arena.providers.models_probe import (
    DiscoveryError,
    append_subscription_entries,
    discover_models,
    propose,
    render,
)
from tests.providers.fakes import fake_codex

CATALOG = "price_version: t\nmodels:\n  - ref: openai-plus/gpt-6.1-sol\n    pricing: subscription\n"


def _provider(body: str) -> Provider:
    return Provider.model_validate(json.loads(body))


CODEX_PROVIDER = _provider(
    '{"id": "openai-plus", "keys": [], "subscription": {"vendor": "chatgpt", "plan": "plus", '
    '"import": "own-sign-in", "runtime": "local-cli"}}'
)
OPENAI = _provider(
    '{"id": "openai", "apis": {"responses": "https://api.example.test/v1"}, '
    '"keys": [{"id": "main", "env": "OPENAI_API_KEY"}]}'
)
ANTHROPIC = _provider(
    '{"id": "anthropic", "apis": {"anthropic": "https://api.example.test"}, '
    '"keys": [{"id": "main", "env": "ANTHROPIC_API_KEY"}]}'
)


@pytest.fixture
def codex_environ(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    binary_dir = tmp_path / "bin"
    home.mkdir()
    binary_dir.mkdir()
    fake_codex(binary_dir)
    signed = tmp_path / "codex-home"
    signed.mkdir()
    (signed / "auth.json").write_text("{}", encoding="utf-8")
    return {
        "HOME": str(home),
        "PATH": str(binary_dir),
        "CODEX_CLI_PATH": str(binary_dir / "codex"),
        "CODEX_HOME": str(signed),
    }


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def test_codex_models_come_from_the_cli_list_and_skip_hidden_ones(
    codex_environ: dict[str, str],
) -> None:
    models = discover_models(CODEX_PROVIDER, codex_environ)
    ids = [model.id for model in models]
    assert "gpt-6.1-sol" in ids
    assert "codex-auto-review" not in ids
    assert {model.source for model in models} == {"cli:codex"}


def test_a_signed_out_cli_is_a_discovery_error(
    codex_environ: dict[str, str], tmp_path: Path
) -> None:
    codex_environ["CODEX_HOME"] = str(tmp_path / "nobody")
    with pytest.raises(DiscoveryError, match="auth_required"):
        discover_models(CODEX_PROVIDER, codex_environ)


def test_claude_cli_has_no_list_so_the_error_says_to_name_models_by_hand() -> None:
    claude = _provider(
        '{"id": "anthropic-max", "keys": [], "subscription": {"vendor": "claude", "plan": "max", '
        '"import": "own-sign-in", "runtime": "local-cli"}}'
    )
    with pytest.raises(DiscoveryError, match="no model list command"):
        discover_models(claude, {})


def test_gateway_runtime_subscription_has_no_list() -> None:
    gateway = _provider(
        '{"id": "anthropic-max", "keys": [], "subscription": {"vendor": "claude", "plan": "max", '
        '"import": "own-sign-in"}}'
    )
    with pytest.raises(DiscoveryError, match="gateway runtime"):
        discover_models(gateway, {})


def test_openai_style_list_uses_the_key_and_the_models_route() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "m-b"}, {"id": "m-a"}, {"id": "m-a"}]})

    models = discover_models(
        OPENAI, {"OPENAI_API_KEY": "sk-test"}, client=_client(httpx.MockTransport(handler))
    )
    assert [model.id for model in models] == ["m-a", "m-b"]
    assert str(seen[0].url) == "https://api.example.test/v1/models"
    assert seen[0].headers["authorization"] == "Bearer sk-test"


def test_anthropic_list_uses_x_api_key_and_the_v1_route() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "claude-x"}]})

    discover_models(
        ANTHROPIC, {"ANTHROPIC_API_KEY": "k"}, client=_client(httpx.MockTransport(handler))
    )
    assert seen[0].url.path == "/v1/models"
    assert seen[0].headers["x-api-key"] == "k"
    assert "anthropic-version" in seen[0].headers


def test_api_errors_never_carry_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad sk-secret-value"})

    with pytest.raises(DiscoveryError) as error:
        discover_models(
            OPENAI,
            {"OPENAI_API_KEY": "sk-secret-value"},
            client=_client(httpx.MockTransport(handler)),
        )
    assert "sk-secret-value" not in str(error.value)
    assert "HTTP 401" in str(error.value)


def test_an_unset_key_variable_is_named_but_nothing_is_sent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    with pytest.raises(DiscoveryError, match="OPENAI_API_KEY is unset"):
        discover_models(OPENAI, {}, client=_client(httpx.MockTransport(handler)))


def test_a_proposed_api_model_has_no_invented_price(tmp_path: Path) -> None:
    catalog = tmp_path / "models.yaml"
    catalog.write_text(CATALOG, encoding="utf-8")
    from arena.providers.models_probe import DiscoveredModel

    proposals = propose(OPENAI, [DiscoveredModel("brand-new", "api:responses")], catalog)
    text = render(proposals)
    assert "ref: openai/brand-new" in text
    assert "price_per_mtok" in text
    assert all(line.lstrip().startswith("#") for line in text.splitlines() if "price" in line)
    # The catalog cannot hold a paid model with no price, so nothing is written for it.
    before = catalog.read_text(encoding="utf-8")
    assert append_subscription_entries(catalog, proposals) == []
    assert catalog.read_text(encoding="utf-8") == before


def test_models_already_in_the_catalog_are_not_proposed(
    codex_environ: dict[str, str], tmp_path: Path
) -> None:
    catalog = tmp_path / "models.yaml"
    catalog.write_text(CATALOG, encoding="utf-8")
    models = discover_models(CODEX_PROVIDER, codex_environ)
    refs = {item.ref for item in propose(CODEX_PROVIDER, models, catalog)}
    assert "openai-plus/gpt-6.1-sol" not in refs
    assert "openai-plus/gpt-6-sol" in refs


def test_write_appends_valid_subscription_entries_and_restores_on_failure(
    codex_environ: dict[str, str], tmp_path: Path
) -> None:
    catalog = tmp_path / "models.yaml"
    catalog.write_text(CATALOG.rstrip("\n"), encoding="utf-8")
    proposals = propose(CODEX_PROVIDER, discover_models(CODEX_PROVIDER, codex_environ), catalog)
    written = append_subscription_entries(catalog, proposals)
    assert written
    loaded = load_catalog(catalog)
    assert {item.ref for item in loaded.models} >= {item.ref for item in written}
    assert all(item.price_per_mtok is None for item in loaded.models)

    broken = tmp_path / "broken.yaml"
    broken.write_text("price_version: t\nmodels:\n  - ref: not-a-ref\n    pricing: subscription\n")
    original = broken.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        append_subscription_entries(broken, proposals)
    assert broken.read_text(encoding="utf-8") == original


def test_providers_models_command_lists_proposes_and_writes(
    codex_environ: dict[str, str], tmp_path: Path
) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text(
        "providers:\n  - id: openai-plus\n    keys: []\n    subscription:\n"
        "      vendor: chatgpt\n      plan: plus\n      import: own-sign-in\n"
        "      runtime: local-cli\n",
        encoding="utf-8",
    )
    catalog = tmp_path / "models.yaml"
    catalog.write_text(CATALOG, encoding="utf-8")
    base = [
        "providers",
        "models",
        "openai-plus",
        "--config",
        str(config),
        "--catalog",
        str(catalog),
    ]
    listed = CliRunner().invoke(app, base, env=codex_environ)
    assert listed.exit_code == 0, listed.output
    assert "gpt-6.1-sol  [in catalog]" in listed.output
    assert "gpt-6-sol  [new]" in listed.output
    assert catalog.read_text(encoding="utf-8") == CATALOG

    written = CliRunner().invoke(app, [*base, "--write"], env=codex_environ)
    assert written.exit_code == 0, written.output
    assert "openai-plus/gpt-6-sol" in catalog.read_text(encoding="utf-8")
    assert {item.ref for item in load_catalog(catalog).models} >= {"openai-plus/gpt-6-sol"}
    assert load_providers(config)


def test_providers_models_command_reports_an_unknown_provider(tmp_path: Path) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text("providers:\n  - id: mock\n    kind: mock\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["providers", "models", "nope", "--config", str(config)])
    assert result.exit_code == 1

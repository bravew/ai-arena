from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from arena.catalog.config import CatalogModel
from arena.cli import app
from arena.cli_gateway import build_gateway_app
from arena.core.modelref import ModelRef
from arena.gateway.endpoints import EndpointResolver, ProviderEndpointError
from arena.gateway.resolve import Target
from arena.providers.config import Provider, ProviderConfig, ProviderKey
from arena.providers.credentials import ApiCredentialStore
from arena.providers.models_probe import discover_models
from arena.runners.metered_gateway import GatewayContainer, GatewayRuntimeError, _docker


def _provider() -> Provider:
    return Provider.model_validate(
        {
            "id": "test",
            "apis": {"chat": "https://dummy.invalid/v1"},
            "keys": [{"id": "main", "env": "${DUMMY_KEY}"}],
        }
    )


def _target(provider: Provider) -> Target:
    return Target(
        ModelRef.parse("test/model"),
        provider,
        CatalogModel.model_validate({"ref": "test/model", "pricing": "subscription"}),
    )


def test_endpoint_resolves_local_selected_candidate_and_environment_precedence(
    tmp_path: Path,
) -> None:
    store = ApiCredentialStore(tmp_path / "credentials")
    store.save("DUMMY_KEY", "dummy-local")
    # If resolution ever reads another candidate it fails, rather than merely returning a value.
    (store.root / "OTHER_KEY").symlink_to(tmp_path / "missing")
    provider = _provider()
    provider.keys.append(ProviderKey(id="other", env="OTHER_KEY"))
    endpoint = EndpointResolver(home=tmp_path, environ={}).resolve(
        _target(provider), protocol="chat", candidate=provider.keys[0]
    )
    assert endpoint.api_key == "dummy-local"
    assert "dummy-local" not in repr(endpoint)
    endpoint = EndpointResolver(home=tmp_path, environ={"DUMMY_KEY": "dummy-env"}).resolve(
        _target(provider), protocol="chat", candidate=provider.keys[0]
    )
    assert endpoint.api_key == "dummy-env"


def test_endpoint_rejects_url_before_local_or_environment_lookup(tmp_path: Path) -> None:
    class Unreadable(dict[str, str]):
        def get(self, *args: object) -> str:
            pytest.fail("must validate endpoint before credential lookup")

    (tmp_path / "credentials").symlink_to(tmp_path / "missing")
    provider = Provider.model_construct(
        id="test",
        keys=[ProviderKey(id="main", env="DUMMY_KEY")],
        apis={"chat": "http://dummy.invalid/v1"},
    )
    with pytest.raises(ProviderEndpointError, match="require HTTPS"):
        EndpointResolver(home=tmp_path, environ=Unreadable()).resolve(
            _target(provider), protocol="chat"
        )


def _gateway(tmp_path: Path, **options: Any) -> GatewayContainer:
    return GatewayContainer(
        project_root=tmp_path,
        run_id="run-1",
        trial_models={"trial-1": "test/model"},
        providers_path=tmp_path / "providers.yaml",
        catalog_path=tmp_path / "catalog.yaml",
        database_path=tmp_path / "db" / "arena.db",
        event_root=tmp_path / "events",
        subscriptions_root=tmp_path / "subscriptions",
        budget_usd=20,
        providers=ProviderConfig(
            providers=[
                _provider(),
                Provider.model_validate(
                    {"id": "unselected", "keys": [{"id": "other", "env": "OTHER_KEY"}]}
                ),
            ]
        ),
        provider_ids={"test"},
        home=tmp_path,
        environ={"PATH": "/dummy/bin"},
        port_factory=lambda: 34567,
        **options,
    )


def test_stored_key_reaches_docker_run_child_only_without_global_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ApiCredentialStore(tmp_path / "credentials")
    store.save("DUMMY_KEY", "dummy-local")
    store.save("OTHER_KEY", "dummy-unused")
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def run(args: tuple[str, ...], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="dummy-container", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.delenv("DUMMY_KEY", raising=False)
    monkeypatch.delenv("OTHER_KEY", raising=False)
    gateway = _gateway(tmp_path, http_get=lambda *args, **kwargs: httpx.Response(200))
    gateway.preflight(["dummy-agent:1"])
    gateway.start()
    gateway.stop()
    docker_run = next((args, kwargs) for args, kwargs in calls if args[1] == "run")
    args, kwargs = docker_run
    assert kwargs["env"] == {"PATH": "/dummy/bin", "DUMMY_KEY": "dummy-local"}
    assert args[args.index("--env") + 1] == "DUMMY_KEY"
    assert args.count("--env") == 1
    assert "dummy-local" not in " ".join(args)
    assert not any("dummy-local" in str(k) for k in kwargs if k != "env")
    assert all(kwargs.get("env") is None for args, kwargs in calls if args[1] != "run")
    assert "DUMMY_KEY" not in os.environ and "OTHER_KEY" not in os.environ


def test_docker_failure_never_echoes_output_with_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, "docker", stderr="dummy-secret")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(GatewayRuntimeError) as error:
        _docker("run", cwd=tmp_path, env={"DUMMY_KEY": "dummy-secret"})
    assert "dummy-secret" not in str(error.value)


def test_gateway_endpoint_guard_runs_before_store_read_or_docker(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, docker=lambda *args, **kwargs: pytest.fail("no Docker"))
    provider = gateway.providers.providers[0]
    assert provider.model_extra is not None
    provider.model_extra["apis"] = {"chat": "http://dummy.invalid/v1"}
    (tmp_path / "credentials").symlink_to(tmp_path / "missing")
    with pytest.raises(GatewayRuntimeError, match="endpoint is unsafe"):
        gateway.preflight([])
    with pytest.raises(GatewayRuntimeError, match="endpoint is unsafe"):
        gateway.start()


def test_providers_test_resolves_explicit_home_and_does_not_print_dummy_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "custom-arena"
    ApiCredentialStore(home / "credentials").save("DUMMY_KEY", "dummy-local")
    config = tmp_path / "providers.yaml"
    config.write_text(
        "providers:\n  - id: test\n    apis: {chat: https://dummy.invalid/v1}\n"
        "    keys: [{id: main, env: '${DUMMY_KEY}'}]\n",
        encoding="utf-8",
    )
    headers: list[dict[str, str]] = []

    def post(url: str, **kwargs: Any) -> httpx.Response:
        headers.append(kwargs["headers"])
        assert not kwargs["follow_redirects"]
        return httpx.Response(200)

    monkeypatch.delenv("DUMMY_KEY", raising=False)
    monkeypatch.setattr(httpx, "post", post)
    result = CliRunner().invoke(
        app, ["providers", "test", "--config", str(config), "--home", str(home)]
    )
    assert result.exit_code == 0, result.output
    assert headers == [{"Authorization": "Bearer dummy-local"}]
    assert "dummy-local" not in result.output


def test_default_arena_home_is_used_by_endpoint_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("DUMMY_KEY", raising=False)
    ApiCredentialStore(tmp_path / ".arena" / "credentials").save("DUMMY_KEY", "dummy-default")
    assert EndpointResolver().resolve(_target(_provider()), protocol="chat").api_key == (
        "dummy-default"
    )


def test_standalone_gateway_passes_explicit_home_to_endpoint_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[EndpointResolver] = []
    monkeypatch.setattr("arena.cli_gateway.load_providers", lambda path: object())
    monkeypatch.setattr("arena.cli_gateway.load_catalog", lambda path: object())

    def create(*args: object, endpoint_resolver: EndpointResolver) -> object:
        seen.append(endpoint_resolver)
        return object()

    monkeypatch.setattr("arena.cli_gateway.create_production_app", create)
    command = build_gateway_app(serve=lambda **kwargs: None)
    result = CliRunner().invoke(command, ["--home", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert seen[0].home == tmp_path


def test_model_listing_reads_only_first_key_from_same_home(tmp_path: Path) -> None:
    ApiCredentialStore(tmp_path / "credentials").save("${DUMMY_KEY}", "dummy-model-list")
    provider = _provider()
    provider.keys.append(ProviderKey(id="other", env="OTHER_KEY"))
    (tmp_path / "credentials" / "OTHER_KEY").symlink_to(tmp_path / "missing")
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": [{"id": "model"}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        listed = discover_models(provider, {}, home=tmp_path, client=client)
    assert [model.id for model in listed] == ["model"]
    assert calls[0].headers["Authorization"] == "Bearer dummy-model-list"


def test_model_list_cli_forwards_explicit_home_without_printing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path | None] = []
    monkeypatch.setattr(
        "arena.cli_providers.load_providers", lambda path: ProviderConfig(providers=[_provider()])
    )

    def discover(provider: Provider, *, home: Path | None) -> list[Any]:
        seen.append(home)
        return []

    monkeypatch.setattr("arena.cli_providers.discover_models", discover)
    config = tmp_path / "providers.yaml"
    config.write_text("providers: []", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "providers",
            "models",
            "test",
            "--home",
            str(tmp_path),
            "--config",
            str(config),
            "--catalog",
            str(tmp_path / "absent.yaml"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen == [tmp_path]

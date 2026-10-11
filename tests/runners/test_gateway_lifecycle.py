from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.providers.config import ProviderConfig
from arena.runners.metered_gateway import GatewayContainer, GatewayRuntimeError


def _providers() -> ProviderConfig:
    return ProviderConfig.model_validate(
        {"providers": [{"id": "anthropic", "keys": [{"id": "main", "env": "TEST_ARENA_KEY"}]}]}
    )


def test_gateway_container_mounts_shared_store_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_ARENA_KEY", "synthetic-key")
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def docker(*args: str, **kwargs: Any) -> str:
        calls.append((args, kwargs))
        return "container-id"

    class Healthy:
        status_code = 200

    gateway = GatewayContainer(
        project_root=tmp_path,
        run_id="run-1",
        trial_models={"trial-1": "anthropic/test"},
        providers_path=tmp_path / "providers.yaml",
        catalog_path=tmp_path / "catalog.yaml",
        database_path=tmp_path / "db" / "arena.db",
        event_root=tmp_path / "events",
        subscriptions_root=tmp_path / "subscriptions",
        budget_usd=20,
        providers=_providers(),
        provider_ids={"anthropic"},
        docker=docker,
        http_get=lambda *args, **kwargs: Healthy(),
        port_factory=lambda: 34567,
    )
    gateway.start()
    assert gateway.running
    run_args = calls[1][0]
    assert any(
        argument == f"type=bind,src={tmp_path / 'db'},dst=/run/data" for argument in run_args
    )
    assert run_args[run_args.index("--env") + 1] == "TEST_ARENA_KEY"
    assert run_args.index("--env") < run_args.index("arena-gateway:dev")
    assert "synthetic-key" not in run_args
    gateway.stop()
    assert not gateway.running
    assert calls[-1][0][:2] == ("rm", "--force")


def test_gateway_container_checks_agent_image_before_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_ARENA_KEY", "synthetic-key")
    docker_calls: list[tuple[str, ...]] = []

    def docker(*args: str, **kwargs: Any) -> str:
        docker_calls.append(args)
        raise GatewayRuntimeError("missing image")

    gateway = GatewayContainer(
        project_root=tmp_path,
        run_id="run-1",
        trial_models={"trial-1": "anthropic/test"},
        providers_path=tmp_path / "providers.yaml",
        catalog_path=tmp_path / "catalog.yaml",
        database_path=tmp_path / "arena.db",
        event_root=tmp_path / "events",
        subscriptions_root=tmp_path / "subscriptions",
        budget_usd=20,
        providers=_providers(),
        provider_ids={"anthropic"},
        docker=docker,
        port_factory=lambda: 34567,
    )
    with pytest.raises(GatewayRuntimeError, match="required agent image is unavailable"):
        gateway.preflight(["arena-agent:test"])
    assert docker_calls[0][:3] == ("image", "inspect", "arena-gateway:dev")
    assert docker_calls[1][:3] == ("image", "inspect", "arena-agent:test")
    assert not any(call[0] in {"build", "run"} for call in docker_calls)


def test_cli_run_dry_run_does_not_start_docker_for_mock_trials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arena.runners.metered_gateway import GatewayContainer

    monkeypatch.setattr(
        GatewayContainer,
        "start",
        lambda self: pytest.fail("mock dry-run must not start gateway"),
    )
    result = CliRunner().invoke(
        app,
        [
            "run",
            "suites/smoke",
            "-c",
            "mock-a",
            "--repeats",
            "1",
            "--dry-run",
            "--db",
            str(tmp_path / "arena.db"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "planned=4" in result.output
    assert not (tmp_path / "arena.db").exists()


def test_resumed_run_budget_subtracts_recorded_spend(tmp_path: Path) -> None:
    from arena.cli_run import _prior_run_spend
    from arena.core.store import Store

    db = tmp_path / "arena.db"
    assert _prior_run_spend(db, "run-1") == 0.0
    with Store(db) as store:
        store.execute("INSERT INTO runs(id, config_json, status) VALUES ('run-1', '{}', 'running')")
        store.execute(
            "INSERT INTO trials(id, run_id, contestant_id, task_id, status, attempt) "
            "VALUES ('t1', 'run-1', 'c', 'task', 'running', 1)"
        )
        for index, cost in enumerate((1.25, 0.5)):
            store.execute(
                "INSERT INTO calls(id, seq, run_id, trial_id, provider, account_id, model_asked, "
                "model_served, status, error_class, tokens_json, cost_usd, details_json) "
                "VALUES (?, ?, 'run-1', 't1', 'p', 'a', 'm', 'm', 'ok', NULL, '{}', ?, '{}')",
                (f"call-{index}", index + 1, cost),
            )
    assert _prior_run_spend(db, "run-1") == 1.75
    assert _prior_run_spend(db, "other") == 0.0

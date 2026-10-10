from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from typer.testing import CliRunner

from arena.cli import app

runner = CliRunner()


def test_cli_registers_gateway_providers_and_doctor() -> None:
    help_text = runner.invoke(app, ["--help"]).output
    assert "gateway" in help_text
    assert "providers" in help_text
    assert "doctor" in help_text


def test_gateway_bench_reports_mock_overhead() -> None:
    result = runner.invoke(app, ["gateway", "bench", "--iterations", "20"])
    assert result.exit_code == 0, result.output
    assert "p50" in result.output
    assert "mock" in result.output.lower()


def test_providers_detect_probes_protocols(monkeypatch: Any) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    monkeypatch.setattr(
        httpx,
        "request",
        lambda method, url, **kwargs: httpx.Client(transport=httpx.MockTransport(handler)).request(
            method, url, **kwargs
        ),
    )
    result = runner.invoke(app, ["providers", "detect", "http://provider.invalid"])
    assert result.exit_code == 0, result.output
    assert "chat" in result.output
    assert "/v1/models" in calls


def test_providers_test_sends_one_token_request_per_key(monkeypatch: Any, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"path": request.url.path, "json": request.read().decode()})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    config = tmp_path / "providers.yaml"
    config.write_text(
        "providers:\n"
        "  - id: local\n"
        "    apis: {chat: http://provider.invalid/v1}\n"
        "    keys: [{id: one, env: ARENA_TEST_KEY}]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ARENA_TEST_KEY", "test-secret")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kwargs: httpx.Client(transport=httpx.MockTransport(handler)).post(
            url, **kwargs
        ),
    )
    result = runner.invoke(app, ["providers", "test", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert '"max_tokens":1' in calls[0]["json"]
    assert "test-secret" not in result.output


def test_gateway_requires_loopback_host() -> None:
    result = runner.invoke(app, ["gateway", "--host", "0.0.0.0"])
    assert result.exit_code != 0
    assert "loopback" in result.output.lower()


def test_doctor_uses_configured_home_and_handles_missing_gateway(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setattr("arena.cli_gateway.gateway_health", lambda url, timeout: False)
    result = runner.invoke(
        app, ["doctor", "--home", str(tmp_path), "--gateway", "http://127.0.0.1:9"]
    )
    assert result.exit_code == 1
    assert "gateway" in result.output.lower()


def test_gateway_command_factory_accepts_injected_runner() -> None:
    from arena.cli_gateway import build_gateway_app

    called: list[tuple[str, int]] = []

    def serve(host: str, port: int, app: Any) -> None:
        called.append((host, port))

    cli_app = build_gateway_app(serve=serve)
    result = CliRunner().invoke(cli_app, ["--host", "127.0.0.1", "--port", "8088"])
    assert result.exit_code == 0, result.output
    assert called == [("127.0.0.1", 8088)]


def test_gateway_command_builds_the_production_app_with_the_subscription_store(
    tmp_path: Path,
) -> None:
    from arena.cli_gateway import build_gateway_app

    served: list[Any] = []

    def serve(app: Any, host: str, port: int) -> None:
        served.append(app)

    config = Path(__file__).parents[2] / "providers.yaml"
    catalog = Path(__file__).parents[2] / "catalog" / "models.yaml"
    cli_app = build_gateway_app(serve=serve, config=config, catalog=catalog)
    result = CliRunner().invoke(cli_app, ["--home", str(tmp_path)])
    assert result.exit_code == 0, result.output

    from starlette.testclient import TestClient

    with TestClient(served[0]) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer arena-trial-24"},
            json={"model": "anthropic/claude-opus-5-5", "messages": []},
        )
    # The production dispatcher is installed: no 501 "not implemented" from a bare app.
    assert response.status_code != 501


def test_providers_import_copies_sign_in_to_private_store(tmp_path: Path) -> None:
    source = Path(__file__).parents[2] / "fixtures" / "subscriptions" / "claude.synthetic.json"
    before = source.read_bytes()

    result = CliRunner().invoke(
        app, ["providers", "import", "claude", "--from", str(source), "--home", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    saved = tmp_path / "subscriptions" / "claude.json"
    assert saved.stat().st_mode & 0o777 == 0o600
    assert source.read_bytes() == before
    assert "synthetic-claude-access" not in result.output


def test_providers_import_rejects_unknown_vendor_and_bad_sign_in(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")

    unknown = CliRunner().invoke(
        app, ["providers", "import", "nope", "--from", str(bad), "--home", str(tmp_path)]
    )
    broken = CliRunner().invoke(
        app, ["providers", "import", "claude", "--from", str(bad), "--home", str(tmp_path)]
    )

    assert unknown.exit_code == 2
    assert broken.exit_code == 1
    assert not (tmp_path / "subscriptions" / "claude.json").exists()

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

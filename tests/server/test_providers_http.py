from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from arena.agents.local import ClaudeLocalAdapter, SignIn
from arena.core.store import Store
from arena.obs.events import EventLog
from arena.providers.accounts import AccountStore
from arena.providers.runtimes import AuthState, RuntimeReport, RuntimeStatus
from arena.runners.local_cli import LocalCliResult
from arena.server.app import ArenaHTTPServer, create_server
from tests.providers.fakes import FIXTURES, fake_claude


class Running:
    def __init__(self, home: Path, providers: Path, catalog: Path, **options: Any) -> None:
        provider_environ = options.pop("environ", None)
        self.server: ArenaHTTPServer = create_server(
            "127.0.0.1",
            0,
            home,
            provider_config=providers,
            provider_catalog=catalog,
            provider_environ=provider_environ,
            **options,
        )
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Running:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def request(
    url: str, *, method: str = "GET", body: bytes | None = None
) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        url, method=method, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        response = urllib.request.urlopen(req, timeout=4)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
    with response:
        payload = response.read()
        return response.status, json.loads(payload) if payload else {}


def setup(tmp_path: Path) -> tuple[Path, Path]:
    providers = tmp_path / "providers.yaml"
    providers.write_text(
        """providers:
  - id: claude-plan
    keys: []
    subscription: {vendor: claude, plan: max, import: own-sign-in, runtime: local-cli}
  - id: missing-api
    apis: {anthropic: https://api.anthropic.com}
    keys: [{id: key, env: API_KEY}]
  - id: disabled
    enabled: false
    apis: {anthropic: https://api.anthropic.com}
    keys: [{id: key, env: API_KEY}]
""",
        encoding="utf-8",
    )
    catalog = tmp_path / "models.yaml"
    catalog.write_text(
        """price_version: test
models:
  - ref: claude-plan/claude-opus-5-5
    protocols: [anthropic]
    pricing: subscription
  - ref: missing-api/claude-sonnet-5-5
    protocols: [anthropic]
    price_per_mtok: {in: 2, out: 10}
    source: https://provider.example/prices
""",
        encoding="utf-8",
    )
    return providers, catalog


def test_provider_list_exposes_runtime_status_and_env_names_only(
    tmp_path: Path, monkeypatch: Any
) -> None:
    providers, catalog = setup(tmp_path)
    secret = "NEVER-RETURN-THIS-SECRET"
    monkeypatch.setenv("API_KEY", secret)

    with Running(
        tmp_path,
        providers,
        catalog,
        environ={"API_KEY": secret, "PATH": "/usr/bin:/bin"},
        provider_detect=lambda _provider: RuntimeReport(
            "claude",
            "claude",
            RuntimeStatus.AUTH_REQUIRED,
            AuthState.UNAUTHENTICATED,
            "/usr/bin/claude",
            "2.1.296",
            "not signed in",
            "claude login",
        ),
    ) as running:
        status, body = request(f"{running.base}/api/providers")

    assert status == 200
    rows = {item["id"]: item for item in body["providers"]}
    assert rows["claude-plan"]["runtime"] == "local-cli"
    assert rows["claude-plan"]["status"] == "auth_required"
    assert rows["claude-plan"]["hint"] == "claude login"
    assert rows["missing-api"]["key_envs"] == ["API_KEY"]
    assert rows["disabled"]["status"] == "disabled"
    assert secret not in json.dumps(body)
    assert body["instructions"]["note"].startswith("Configure providers and models")
    assert rows["missing-api"]["credentials"][0]["source"] == "environment"
    assert body["capabilities"]["settings"] is True


def test_cli_connection_test_records_usage_and_finishes_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    providers, catalog_path = setup(tmp_path)
    cli_dir = tmp_path / "bin"
    cli_dir.mkdir()
    binary = fake_claude(cli_dir, auth=str(FIXTURES / "claude" / "auth-status.signed-in.json"))
    accounts = AccountStore(tmp_path / "accounts")
    accounts.save("claude", SignIn(files={".claude/.credentials.json": b"{}"}), "test")
    (tmp_path / ".signed-in").write_text("signed in", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_PATH", str(binary))
    monkeypatch.setenv("ARENA_DISCOVER_SIGNINS", "0")

    def runner(cli_request: Any) -> LocalCliResult:
        assert cli_request.binary == str(binary)
        assert cli_request.model == "claude-opus-5-5"
        summary = ClaudeLocalAdapter().parse(
            (FIXTURES / "claude" / "stream.synthetic.jsonl").read_text().splitlines()
        )
        return LocalCliResult(
            failure=None,
            detail="ok",
            exit_code=0,
            summary=summary,
            outputs={},
            duration_seconds=0.02,
            model_served_verified=True,
        )

    with Running(
        tmp_path,
        providers,
        catalog_path,
        environ={
            "PATH": str(cli_dir),
            "CLAUDE_CODE_PATH": str(binary),
            "HOME": str(tmp_path),
            "ARENA_DISCOVER_SIGNINS": "0",
        },
        provider_detect=lambda _provider: RuntimeReport(
            "claude",
            "claude",
            RuntimeStatus.OK,
            AuthState.AUTHENTICATED,
            str(binary),
            "2.1.296",
            "ready",
            None,
        ),
        provider_test_runner=runner,
    ) as running:
        status, body = request(
            f"{running.base}/api/providers/claude-plan/test", method="POST", body=b"{}"
        )

    assert status == 200 and body["ok"] is True, body
    assert body["source"] == "cli_reported"
    with Store(tmp_path / "arena.db") as store:
        assert store.execute("SELECT status FROM runs").fetchone()[0] == "succeeded"
        assert store.execute("SELECT status FROM trials").fetchone()[0] == "succeeded"
        assert store.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1
    assert list((tmp_path / "provider-tests").glob("*/events.jsonl"))
    # A finished test must release the event-log writer lock, or the next test is refused and
    # the viewer only sees "Failed to fetch".
    EventLog(tmp_path / "provider-tests").close()


def test_connection_test_is_rate_limited_and_does_not_repeat_request(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import arena.server.providers_api as provider_api

    providers, catalog = setup(tmp_path)
    calls: list[int] = []
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setattr(provider_api, "_test_api_request", lambda *_args: calls.append(200) or 200)

    with Running(tmp_path, providers, catalog) as running:
        first = request(f"{running.base}/api/providers/missing-api/test", method="POST", body=b"{}")
        second = request(
            f"{running.base}/api/providers/missing-api/test", method="POST", body=b"{}"
        )

    assert first[0] == 200
    assert second[0] == 429
    assert len(calls) == 1


def test_connection_test_rejects_plaintext_endpoint_before_request(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import arena.server.providers_api as provider_api

    providers, catalog = setup(tmp_path)
    raw = providers.read_text(encoding="utf-8").replace(
        "https://api.anthropic.com", "http://api.anthropic.com"
    )
    providers.write_text(raw, encoding="utf-8")
    monkeypatch.setenv("API_KEY", "DO-NOT-SEND-THIS")
    requests: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        provider_api.httpx,
        "post",
        lambda *args, **kwargs: requests.append((args, kwargs)),
    )

    with Running(tmp_path, providers, catalog) as running:
        status, body = request(
            f"{running.base}/api/providers/missing-api/test", method="POST", body=b"{}"
        )

    assert status == 422
    assert body["ok"] is False
    assert "HTTPS" in body["error"]
    assert "DO-NOT-SEND-THIS" not in json.dumps(body)
    assert requests == []


def test_api_request_rejects_insecure_or_malformed_urls_without_transport(
    monkeypatch: Any,
) -> None:
    import arena.server.providers_api as provider_api

    requests: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        provider_api.httpx,
        "post",
        lambda *args, **kwargs: requests.append((args, kwargs)),
    )
    for endpoint in (
        "http://api.example.test/v1",
        "ftp://api.example.test/v1",
        "https://user:password@api.example.test/v1",
        "https://api.example.test:bad/v1",
        "/v1",
    ):
        with pytest.raises(ValueError, match="absolute HTTPS URL"):
            provider_api._test_api_request("anthropic", endpoint, "sentinel")
    assert requests == []


def test_api_request_does_not_follow_https_redirects(monkeypatch: Any) -> None:
    import arena.server.providers_api as provider_api

    class Response:
        status_code = 302

    calls: list[tuple[Any, ...]] = []

    def post(*args: Any, **kwargs: Any) -> Response:
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr(provider_api.httpx, "post", post)
    assert (
        provider_api._test_api_request("anthropic", "https://api.example.test/v1", "sentinel")
        == 302
    )
    assert len(calls) == 1
    assert calls[0][1]["follow_redirects"] is False


def test_provider_test_rejects_disabled_and_unknown_ids(tmp_path: Path) -> None:
    providers, catalog = setup(tmp_path)
    with Running(tmp_path, providers, catalog) as running:
        disabled = request(f"{running.base}/api/providers/disabled/test", method="POST", body=b"{}")
        unknown = request(
            f"{running.base}/api/providers/no-such-provider/test", method="POST", body=b"{}"
        )
    assert disabled[0] == 409
    assert unknown[0] == 404

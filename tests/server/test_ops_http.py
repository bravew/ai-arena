from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from arena.providers.runtimes import AuthState, RuntimeReport, RuntimeStatus
from arena.server.app import ArenaHTTPServer, create_server
from tests.server.seed_ops_home import seed


def _get(url: str, cookie: str | None = None) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"Cookie": cookie} if cookie else {})
    try:
        response = urllib.request.urlopen(request, timeout=3)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
    with response:
        return response.status, json.loads(response.read())


class Running:
    def __init__(self, home: Path, *, run_key: str | None = None) -> None:
        self.reports = [
            RuntimeReport(
                runtime="claude",
                vendor="claude",
                status=RuntimeStatus.OK,
                auth=AuthState.UNAUTHENTICATED,
                binary="/usr/bin/claude",
                version="2.1.296",
                detail="not signed in",
                hint="claude login",
            )
        ]
        self.server: ArenaHTTPServer = create_server(
            "127.0.0.1",
            0,
            home,
            run_key=run_key,
            session_secret=b"ops-test-secret",
            ops_detect=lambda: self.reports,
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


def test_ops_returns_live_calls_meters_provider_status_and_sources(tmp_path: Path) -> None:
    seed(tmp_path)

    with Running(tmp_path) as running:
        status, body = _get(f"{running.base}/api/ops")

    assert status == 200
    assert len(body["calls"]) == 2
    cli_call = next(call for call in body["calls"] if call["upstream"] == "cli_reported")
    assert cli_call["protocol_in"] == "local-cli"
    assert cli_call["account_id"] == "0123abcd"
    assert body["meters"][0]["source"] == "arena-counted"
    assert body["meters"][0]["limit"] is None
    assert body["providers"][0]["status"] == "ok"
    assert body["lanes"] == {}
    assert "unavailable" in body["sources"]["lanes"]
    assert body["hooks"] == []


def test_ops_requires_run_key_when_server_is_remote_mode(tmp_path: Path) -> None:
    server = create_server(
        "0.0.0.0",
        0,
        tmp_path,
        run_key="secret-key",
        trusted_proxy="127.0.0.1",
        session_secret=b"ops-test-secret",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(f"http://127.0.0.1:{server.server_address[1]}/api/ops")
        assert status == 401
        assert body["error"] == "sign in required"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

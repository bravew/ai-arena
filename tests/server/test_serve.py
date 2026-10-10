from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from arena.server.app import create_server


def request(
    url: str, *, method: str = "GET", headers: dict[str, str] | None = None
) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        response = urllib.request.urlopen(req, timeout=2)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers.items())
    with response:
        return response.status, response.read(), dict(response.headers.items())


def run(server) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def test_default_server_serves_api_and_artifacts_on_separate_origins(tmp_path: Path) -> None:
    app_server = create_server("127.0.0.1", 0, tmp_path)
    artifact_server = create_server("127.0.0.1", 0, tmp_path, artifacts=True)
    run(app_server)
    run(artifact_server)
    try:
        host, port = app_server.server_address[:2]
        status, body, _ = request(f"http://{host}:{port}/api/health")
        assert status == 200
        assert json.loads(body) == {"status": "ok"}
        assert artifact_server.server_address[1] != port
    finally:
        app_server.shutdown()
        artifact_server.shutdown()
        app_server.server_close()
        artifact_server.server_close()


def test_api_refuses_cross_origin_post(tmp_path: Path) -> None:
    server = create_server("127.0.0.1", 0, tmp_path)
    run(server)
    try:
        host, port = server.server_address[:2]
        status, _, _ = request(
            f"http://{host}:{port}/api/runs/example/cancel",
            method="POST",
            headers={"Origin": "https://foreign.example"},
        )
        assert status == 403
    finally:
        server.shutdown()
        server.server_close()


def test_registered_post_route_is_dispatched_after_origin_check(tmp_path: Path) -> None:
    server = create_server("127.0.0.1", 0, tmp_path)
    called: list[str] = []

    def vote(handler, match: str) -> None:
        called.append(match)
        handler.send_json(201, {"vote": match})

    register_route = server.register_route
    register_route("POST", r"/api/votes/([A-Za-z0-9_-]+)", vote)
    run(server)
    try:
        host, port = server.server_address[:2]
        base = f"http://{host}:{port}"
        status, body, _ = request(f"{base}/api/votes/pair-1", method="POST")
        assert status == 201
        assert json.loads(body) == {"vote": "pair-1"}
        assert called == ["pair-1"]

        status, _, _ = request(
            f"{base}/api/votes/pair-2",
            method="POST",
            headers={"Origin": "https://foreign.example"},
        )
        assert status == 403
        assert called == ["pair-1"]
    finally:
        server.shutdown()
        server.server_close()


def test_long_poll_returns_events_after_cursor(tmp_path: Path) -> None:
    run_id = "run-example"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    event = {
        "event_version": 1,
        "run_id": run_id,
        "seq": 1,
        "kind": "run_started",
        "ts": "2026-01-01T00:00:00Z",
        "ref": None,
        "data": {"suite_id": "smoke"},
    }
    (run_dir / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    server = create_server("127.0.0.1", 0, tmp_path)
    run(server)
    try:
        host, port = server.server_address[:2]
        status, body, _ = request(f"http://{host}:{port}/api/runs/{run_id}/events?after=0&wait=0")
        assert status == 200
        assert isinstance(json.loads(body), list)
        assert json.loads(body) == [event]
    finally:
        server.shutdown()
        server.server_close()

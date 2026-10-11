from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from arena.server.app import create_server, serve


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
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


def test_remote_serve_exposes_artifact_listener_through_trusted_proxy(
    tmp_path: Path, monkeypatch
) -> None:
    api = create_server("0.0.0.0", 0, tmp_path, run_key="test-run-key", trusted_proxy="127.0.0.1")
    artifact = create_server(
        "0.0.0.0", 0, tmp_path, run_key="test-run-key", trusted_proxy="127.0.0.1"
    )
    calls = []

    def create(*args, **kwargs):
        calls.append((args, kwargs))
        return api if len(calls) == 1 else artifact

    monkeypatch.setattr("arena.server.app.create_server", create)
    monkeypatch.setattr(api, "serve_forever", lambda: None)
    monkeypatch.setattr(api, "shutdown", lambda: None)
    monkeypatch.setattr(artifact, "serve_forever", lambda: None)
    monkeypatch.setattr(artifact, "shutdown", lambda: None)

    serve("0.0.0.0", 7400, 7402, tmp_path, "test-run-key", "127.0.0.1")

    assert calls[0][0][:2] == ("0.0.0.0", 7400)
    assert calls[1][0][:2] == ("0.0.0.0", 7402)
    assert calls[0][1]["session_secret"] == calls[1][1]["session_secret"]
    assert calls[1][1]["artifacts"] is True
    assert calls[1][1]["trusted_proxy"] == "127.0.0.1"
    api.server_close()
    artifact.server_close()


def test_remote_artifact_requires_the_api_signed_session_cookie(tmp_path: Path) -> None:
    from arena.core.cas import ArtifactStore

    run_key = "test-run-key"
    session_secret = b"fixture-session-secret" * 2
    api = create_server(
        "0.0.0.0",
        0,
        tmp_path,
        run_key=run_key,
        trusted_proxy="127.0.0.1",
        session_secret=session_secret,
    )
    artifact = create_server(
        "0.0.0.0",
        0,
        tmp_path,
        run_key=run_key,
        artifacts=True,
        trusted_proxy="127.0.0.1",
        session_secret=session_secret,
    )
    payload = b"<html><script>top.location='https://api.example/';</script>artifact</html>"
    digest = ArtifactStore(tmp_path / "artifacts").put(payload)
    from arena.core.store import Store

    store = Store(tmp_path / "arena.db")
    store.execute(
        "INSERT INTO runs (id, config_json, status) VALUES ('fixture', '{}', 'succeeded')"
    )
    store.execute(
        "INSERT INTO trials (id, run_id, contestant_id, task_id, status) "
        "VALUES ('trial', 'fixture', 'contestant', 'task', 'succeeded')"
    )
    store.execute(
        "INSERT INTO trial_artifacts(trial_id, path, sha256, mime, render_hint) "
        "VALUES ('trial', 'index.html', ?, 'text/html', 'html-sandbox')",
        (digest,),
    )
    store.close()
    run(api)
    run(artifact)
    try:
        api_port = api.server_address[1]
        artifact_port = artifact.server_address[1]
        status, _, _ = request(f"http://127.0.0.1:{artifact_port}/artifacts/{digest}")
        assert status == 401
        status, _, headers = request(
            f"http://127.0.0.1:{api_port}/auth/sign-in",
            method="POST",
            headers={"Content-Type": "application/json", "X-Forwarded-Proto": "https"},
            body=b'{"key":"test-run-key"}',
        )
        assert status == 204
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, body, response_headers = request(
            f"http://127.0.0.1:{artifact_port}/artifacts/{digest}",
            headers={"Cookie": cookie},
        )
        assert status == 200 and body == payload
        assert response_headers["Content-Type"] == "text/html"
        policy = response_headers["Content-Security-Policy"]
        assert "sandbox" in policy and "allow-scripts" not in policy
        assert "allow-same-origin" not in policy
        assert response_headers["X-Frame-Options"] == "DENY"
        assert response_headers["Cross-Origin-Resource-Policy"] == "cross-origin"

        with Store(tmp_path / "arena.db") as store:
            store.execute(
                "UPDATE trial_artifacts SET mime = ? WHERE sha256 = ?",
                ("image/png\r\nAccess-Control-Allow-Origin: *", digest),
            )
        status, _, response_headers = request(
            f"http://127.0.0.1:{artifact_port}/artifacts/{digest}",
            headers={"Cookie": cookie},
        )
        assert status == 200
        assert response_headers["Content-Type"] == "application/octet-stream"
        assert "Access-Control-Allow-Origin" not in response_headers
    finally:
        api.shutdown()
        artifact.shutdown()
        api.server_close()
        artifact.server_close()


def test_loopback_run_key_sign_in_unlocks_artifacts(tmp_path: Path) -> None:
    from arena.core.cas import ArtifactStore

    secret = b"local-sign-in-test-secret"
    key = "local-test-key"
    digest = ArtifactStore(tmp_path / "artifacts").put(b"protected local artifact")
    api = create_server("127.0.0.1", 0, tmp_path, run_key=key, session_secret=secret)
    artifact = create_server(
        "127.0.0.1", 0, tmp_path, run_key=key, session_secret=secret, artifacts=True
    )
    run(api)
    run(artifact)
    try:
        api_url = f"http://127.0.0.1:{api.server_address[1]}"
        artifact_url = f"http://127.0.0.1:{artifact.server_address[1]}/artifacts/{digest}"
        assert request(artifact_url)[0] == 401
        assert request(api_url + "/auth/sign-in", method="POST", body=b'{"key":"wrong"}')[0] == 401
        status, _, headers = request(
            api_url + "/auth/sign-in", method="POST", body=b'{"key":"local-test-key"}'
        )
        assert status == 204
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        assert "HttpOnly" in headers["Set-Cookie"] and "Secure" in headers["Set-Cookie"]
        assert request(artifact_url, headers={"Cookie": cookie})[:2] == (
            200,
            b"protected local artifact",
        )
        assert (
            request(
                api_url + "/auth/sign-in",
                method="POST",
                body=b'{"key":"local-test-key"}',
                headers={"Origin": "https://foreign.example"},
            )[0]
            == 403
        )
    finally:
        api.shutdown()
        artifact.shutdown()
        api.server_close()
        artifact.server_close()


def test_default_server_serves_api_and_artifacts_on_separate_origins(tmp_path: Path) -> None:
    from arena.core.cas import ArtifactStore

    digest = ArtifactStore(tmp_path / "artifacts").put(b"local artifact")
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
        artifact_port = artifact_server.server_address[1]
        status, body, _ = request(f"http://127.0.0.1:{artifact_port}/artifacts/{digest}")
        assert status == 200
        assert body == b"local artifact"
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

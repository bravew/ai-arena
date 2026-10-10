from __future__ import annotations

import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from arena.server.app import create_server


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    opener = urllib.request.build_opener(NoRedirect())
    try:
        response = opener.open(req, timeout=2)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers.items())
    with response:
        return response.status, response.read(), dict(response.headers.items())


def start(server) -> None:
    threading.Thread(target=server.serve_forever, daemon=True).start()


def test_remote_server_requires_key_and_tls_for_post_sign_in(tmp_path: Path) -> None:
    server = create_server(
        "0.0.0.0", 0, tmp_path, run_key="test-run-key", trusted_proxy="127.0.0.1"
    )
    start(server)
    try:
        _, port = server.server_address[:2]
        base = f"http://127.0.0.1:{port}"
        status, _, _ = request(f"{base}/api/health")
        assert status == 401
        status, _, _ = request(f"{base}/auth/sign-in?key=test-run-key")
        assert status == 405
        status, _, _ = request(
            f"{base}/auth/sign-in",
            method="POST",
            headers={"Content-Type": "application/json", "X-Forwarded-Proto": "http"},
            body=b'{"key":"test-run-key"}',
        )
        assert status == 400
        status, _, headers = request(
            f"{base}/auth/sign-in",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Forwarded-Proto": "https",
            },
            body=b'{"key":"test-run-key"}',
        )
        assert status == 204
        assert "HttpOnly" in headers["Set-Cookie"]
        assert "Secure" in headers["Set-Cookie"]
        assert "key=" not in headers.get("Location", "")
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, _ = request(f"{base}/api/health", headers={"Cookie": cookie})
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_remote_server_requires_trusted_proxy_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="trusted TLS-terminating proxy"):
        create_server("0.0.0.0", 0, tmp_path, run_key="test-run-key")
    with pytest.raises(ValueError, match="proxy must be an IP address"):
        create_server("0.0.0.0", 0, tmp_path, run_key="test-run-key", trusted_proxy="proxy.local")


def test_remote_server_rejects_cross_origin_post(tmp_path: Path) -> None:
    server = create_server(
        "0.0.0.0", 0, tmp_path, run_key="test-run-key", trusted_proxy="127.0.0.1"
    )
    start(server)
    try:
        _, port = server.server_address[:2]
        base = f"http://127.0.0.1:{port}"
        status, _, headers = request(
            f"{base}/auth/sign-in",
            method="POST",
            headers={"Content-Type": "application/json", "X-Forwarded-Proto": "https"},
            body=b'{"key":"test-run-key"}',
        )
        assert status == 204
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, _ = request(
            f"{base}/api/runs/example/cancel",
            method="POST",
            headers={"Cookie": cookie, "Origin": "https://foreign.example"},
        )
        assert status == 403
    finally:
        server.shutdown()
        server.server_close()

"""Standard-library HTTP server for the local Arena viewer and artifacts."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, unquote, urlsplit

from arena.core.bundle import BundleError, build_bundle, default_home, open_run
from arena.core.bundle_contract import validate_events_jsonl
from arena.core.store import Store

_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class ArenaHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    arena_routes: list[tuple[str, str, Callable[..., None]]]
    register_route: Callable[[str, str, Callable[..., None]], None]


def create_server(
    host: str = "127.0.0.1",
    port: int = 7400,
    home: Path | None = None,
    *,
    run_key: str | None = None,
    artifacts: bool = False,
    trusted_proxy: str | None = None,
) -> ArenaHTTPServer:
    """Create a server instance; ``serve_forever`` starts it for real."""
    data_home = home or default_home()
    off_box = host not in {"127.0.0.1", "localhost", "::1"}
    if off_box and not run_key:
        raise ValueError("binding off-box requires ARENA_RUN_KEY")
    if off_box and not trusted_proxy:
        raise ValueError("off-box binding requires a trusted TLS-terminating proxy")
    if trusted_proxy is not None:
        try:
            trusted_proxy = str(ipaddress.ip_address(trusted_proxy))
        except ValueError as error:
            raise ValueError("trusted proxy must be an IP address") from error
    secret = secrets.token_bytes(32)
    routes: list[tuple[str, str, Callable[..., None]]] = []

    def route(method: str, path: str, callback: Callable[..., None]) -> None:
        routes.append((method, path, callback))

    class Handler(BaseHTTPRequestHandler):
        server_version = "Arena/1"

        def log_message(self, format: str, *_args: object) -> None:
            return

        def send_bytes(
            self,
            status: int,
            body: bytes,
            content_type: str = "application/json",
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def send_json(
            self, status: int, value: object, headers: dict[str, str] | None = None
        ) -> None:
            self.send_bytes(status, json.dumps(value).encode(), headers=headers)

        def authenticated(self) -> bool:
            if not off_box:
                return True
            cookie = next(
                (
                    part.strip().removeprefix("arena_session=")
                    for part in self.headers.get("Cookie", "").split(";")
                    if part.strip().startswith("arena_session=")
                ),
                "",
            )
            expected = hmac.new(secret, (run_key or "").encode(), hashlib.sha256).hexdigest()
            return bool(cookie) and hmac.compare_digest(cookie, expected)

        def request_is_https(self) -> bool:
            if not off_box:
                return True
            return bool(
                trusted_proxy
                and self.client_address[0] == trusted_proxy
                and self.headers.get("X-Forwarded-Proto", "").lower() == "https"
            )

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if path == "/auth/sign-in":
                self.send_json(405, {"error": "use POST to sign in"}, {"Allow": "POST"})
                return
            if off_box and not self.authenticated():
                self.send_json(401, {"error": "sign in required"}, {"WWW-Authenticate": "Arena"})
                return
            if artifacts:
                self.serve_artifact(path)
                return
            for method, pattern, callback in routes:
                match = re.fullmatch(pattern, path)
                if method == "GET" and match:
                    callback(self, match.group(1) if match.groups() else "")
                    return
            self.serve_static(path)

        def serve_static(self, path: str) -> None:
            web_root = Path(__file__).resolve().parents[3] / "web" / "dist"
            relative = Path(path.lstrip("/") or "index.html")
            target = (web_root / relative).resolve()
            if not target.is_relative_to(web_root.resolve()) or not target.is_file():
                self.send_json(404, {"error": "not found"})
                return
            content_types = {
                ".html": "text/html; charset=utf-8",
                ".js": "text/javascript",
                ".css": "text/css",
                ".svg": "image/svg+xml",
                ".json": "application/json",
            }
            self.send_bytes(
                200,
                target.read_bytes(),
                content_types.get(target.suffix, "application/octet-stream"),
            )

        def do_POST(self) -> None:
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if path == "/auth/sign-in":
                if self.headers.get("Origin"):
                    self.send_json(403, {"error": "cross-origin sign-in is refused"})
                    return
                if not off_box:
                    self.send_json(404, {"error": "not found"})
                    return
                if not self.request_is_https():
                    self.send_json(400, {"error": "TLS is required for sign in"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 4096:
                        raise ValueError("invalid body size")
                    payload = cast(object, json.loads(self.rfile.read(length)))
                    key = (
                        cast(dict[str, object], payload).get("key")
                        if isinstance(payload, dict)
                        else None
                    )
                except (ValueError, json.JSONDecodeError):
                    self.send_json(400, {"error": "invalid sign-in request"})
                    return
                if not isinstance(key, str) or not run_key or not hmac.compare_digest(key, run_key):
                    self.send_json(401, {"error": "invalid run key"})
                    return
                token = hmac.new(secret, run_key.encode(), hashlib.sha256).hexdigest()
                self.send_bytes(
                    204,
                    b"",
                    headers={
                        "Set-Cookie": (
                            f"arena_session={token}; HttpOnly; Secure; SameSite=Strict; Path=/"
                        ),
                    },
                )
                return
            if self.headers.get("Origin"):
                self.send_json(403, {"error": "cross-origin writes are refused"})
                return
            if off_box and not self.authenticated():
                self.send_json(401, {"error": "sign in required"})
                return
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            for method, pattern, callback in routes:
                match = re.fullmatch(pattern, path)
                if method == "POST" and match:
                    callback(self, match.group(1) if match.groups() else "")
                    return
            self.send_json(404, {"error": "not found"})

        def serve_artifact(self, path: str) -> None:
            if path == "/":
                self.send_json(200, {"service": "arena-artifacts"})
                return
            if path.startswith("/artifacts/"):
                digest = path.removeprefix("/artifacts/")
                if not re.fullmatch(r"[a-f0-9]{64}", digest):
                    self.send_json(404, {"error": "not found"})
                    return
                try:
                    from arena.core.cas import ArtifactStore

                    payload = ArtifactStore(data_home / "artifacts").get(digest)
                except (OSError, ValueError):
                    self.send_json(404, {"error": "not found"})
                    return
                self.send_bytes(
                    200,
                    payload,
                    "application/octet-stream",
                    {"Content-Security-Policy": "default-src 'none'; sandbox"},
                )
                return
            self.send_json(404, {"error": "not found"})

    server = ArenaHTTPServer((host, port), Handler)
    server.arena_routes = routes  # type: ignore[attr-defined]
    server.register_route = route  # type: ignore[attr-defined]

    def health(handler: Handler, _match: str) -> None:
        handler.send_json(200, {"status": "ok"})

    def runs(handler: Handler, _match: str) -> None:
        try:
            db = data_home / "arena.db"
            if not db.exists():
                handler.send_json(200, {"runs": []})
                return
            with Store(db) as store:
                rows = store.execute(
                    "SELECT id, status, created_at FROM runs ORDER BY created_at DESC"
                ).fetchall()
            handler.send_json(200, {"runs": [dict(row) for row in rows]})
        except Exception as error:
            handler.send_json(500, {"error": f"cannot read runs: {error}"})

    def bundle(handler: Handler, run_id: str) -> None:
        if not _RUN_ID.fullmatch(run_id):
            handler.send_json(404, {"error": "run not found"})
            return
        try:
            handler.send_json(200, build_bundle(open_run(data_home, run_id)))
        except BundleError as error:
            handler.send_json(404, {"error": str(error)})

    def events(handler: Handler, run_id: str) -> None:
        if not _RUN_ID.fullmatch(run_id):
            handler.send_json(404, {"error": "run not found"})
            return
        query = parse_qs(urlsplit(handler.path).query)
        try:
            after = max(0, int(query.get("after", ["0"])[0]))
            wait = min(25.0, max(0.0, float(query.get("wait", ["25"])[0])))
        except ValueError:
            handler.send_json(400, {"error": "invalid cursor or wait"})
            return
        event_path = data_home / "runs" / run_id / "events.jsonl"
        deadline = time.monotonic() + wait
        while True:
            try:
                contents = event_path.read_text(encoding="utf-8")
                validate_events_jsonl(contents)
                rows = [json.loads(line) for line in contents.splitlines() if line.strip()]
            except FileNotFoundError:
                handler.send_json(404, {"error": "run events not found"})
                return
            except (OSError, ValueError) as error:
                handler.send_json(500, {"error": f"cannot read run events: {error}"})
                return
            selected = [row for row in rows if row["seq"] > after]
            if selected or time.monotonic() >= deadline:
                handler.send_json(200, selected)
                return
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    route("GET", r"/api/health", health)
    route("GET", r"/api/runs", runs)
    route("GET", r"/api/runs/([A-Za-z0-9._-]+)/bundle", bundle)
    route("GET", r"/api/runs/([A-Za-z0-9._-]+)/events", events)
    return server


def serve(
    host: str = "127.0.0.1",
    port: int = 7400,
    artifact_port: int = 7402,
    home: Path | None = None,
    run_key: str | None = None,
    trusted_proxy: str | None = None,
) -> None:
    """Run viewer/API and artifact servers on separate ports."""
    api = create_server(host, port, home, run_key=run_key, trusted_proxy=trusted_proxy)
    artifact_host = "127.0.0.1" if host not in {"127.0.0.1", "localhost", "::1"} else host
    artifact_server = create_server(
        artifact_host, artifact_port, home, run_key=run_key, artifacts=True
    )
    artifact_thread = threading.Thread(target=artifact_server.serve_forever, daemon=True)
    artifact_thread.start()
    try:
        api.serve_forever()
    finally:
        api.shutdown()
        artifact_server.shutdown()
        api.server_close()
        artifact_server.server_close()

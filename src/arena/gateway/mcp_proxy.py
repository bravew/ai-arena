"""`/mcp/<name>`: the only way a trial container reaches a kit's URL MCP servers (DEV_PLAN §6.5).

The daemon holds the upstream URL and resolves `${ENV}` references in the configured headers, so
MCP secrets never enter the container, and container egress stays gateway-only. Each JSON-RPC
request becomes one `McpSpan` with server, tool, duration and status.

This module is the proxy core. It has no web framework and no HTTP client of its own: the gateway
server mounts `McpProxy.handle` on `/mcp/{name}` after it has checked the trial token, and passes
the transport it already uses for providers. `UrllibUpstream` is a dependency-free default for
tests and small runs; it buffers the whole reply, so it does not stream.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from arena.core.models import McpServer

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# What the container may choose. Everything else, including its Authorization header (the trial
# token) and cookies, is dropped; the daemon's configured headers are added after these.
_FORWARDED_REQUEST = frozenset({"accept", "content-type", "mcp-session-id", "mcp-protocol-version"})
_RETURNED_REPLY = frozenset({"content-type", "mcp-session-id", "retry-after"})
_MAX_REQUEST = 4 * 1024 * 1024
_MAX_CAPTURE = 1024 * 1024


class McpConfigError(ValueError):
    """The kit's MCP servers cannot be served: an unset variable or an unusable URL."""


@dataclass(frozen=True)
class McpSpan:
    """One MCP request, shaped for a `tool` span."""

    trial_id: str
    server: str
    tool: str
    duration_ms: int
    status: str  # "ok" | "error"


@dataclass(frozen=True)
class UpstreamReply:
    status_code: int
    headers: Mapping[str, str]
    body: AsyncIterator[bytes]


class McpUpstream(Protocol):
    async def post(self, url: str, *, body: bytes, headers: Mapping[str, str]) -> UpstreamReply: ...


SpanSink = Callable[[McpSpan], None]


@dataclass(frozen=True)
class ProxyReply:
    status_code: int
    headers: Mapping[str, str]
    body: AsyncIterator[bytes]


@dataclass(frozen=True)
class _Route:
    url: str
    headers: Mapping[str, str]


def resolve_routes(servers: Sequence[McpServer], env: Mapping[str, str]) -> dict[str, _Route]:
    """Resolve `${ENV}` in headers on the daemon, failing on every unset name at once."""
    routes: dict[str, _Route] = {}
    missing: set[str] = set()
    for server in servers:
        if not server.url:
            continue
        parts = urlsplit(server.url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
            raise McpConfigError(f"mcp {server.name}: url must be http(s) without credentials")
        headers: dict[str, str] = {}
        for key, value in server.headers.items():
            missing.update(name for name in _ENV_REFERENCE.findall(value) if not env.get(name))
            headers[key] = _ENV_REFERENCE.sub(lambda m: env.get(m.group(1), ""), value)
        routes[server.name] = _Route(server.url, headers)
    if missing:
        listed = ", ".join(f"${{{name}}}" for name in sorted(missing))
        raise McpConfigError(f"mcp servers need unset environment variables: {listed}")
    return routes


class McpProxy:
    def __init__(
        self,
        servers: Sequence[McpServer],
        *,
        env: Mapping[str, str],
        upstream: McpUpstream,
        spans: SpanSink | None = None,
    ) -> None:
        self._routes = resolve_routes(servers, env)
        self._upstream = upstream
        self._spans = spans

    def names(self) -> tuple[str, ...]:
        return tuple(self._routes)

    async def handle(
        self, name: str, body: bytes, headers: Mapping[str, str], *, trial_id: str
    ) -> ProxyReply:
        """Forward one JSON-RPC POST. The caller has already authenticated the trial token."""
        route = self._routes.get(name)
        if route is None:
            return _error(404, "unknown_mcp_server", "no such MCP server in this kit")
        if len(body) > _MAX_REQUEST:
            return _error(413, "request_too_large", "MCP request body is too large")
        tools = _tool_names(body)
        if tools is None:
            return _error(400, "invalid_request", "body must be a JSON-RPC message")

        started = time.monotonic()
        sent = {key.lower(): value for key, value in headers.items()}
        forwarded = {key: value for key, value in sent.items() if key in _FORWARDED_REQUEST}
        # Configured headers last, and case-insensitively, so the container cannot override them.
        configured = {key.lower(): value for key, value in route.headers.items()}
        forwarded.update(configured)

        def record(status: str) -> None:
            if self._spans is None:
                return
            elapsed = max(0, round((time.monotonic() - started) * 1000))
            for tool in tools:
                self._spans(McpSpan(trial_id, name, tool, elapsed, status))

        try:
            reply = await self._upstream.post(route.url, body=body, headers=forwarded)
        except Exception:
            record("error")
            return _error(502, "mcp_upstream_error", "MCP server request failed")
        returned = {k: v for k, v in reply.headers.items() if k.lower() in _RETURNED_REPLY}

        async def stream() -> AsyncIterator[bytes]:
            captured = bytearray()
            ok = 200 <= reply.status_code < 300
            try:
                async for chunk in reply.body:
                    if len(captured) < _MAX_CAPTURE:
                        captured.extend(chunk[: _MAX_CAPTURE - len(captured)])
                    yield chunk
            except BaseException:
                ok = False
                raise
            finally:
                json_reply = returned.get("content-type", "").startswith("application/json")
                if ok and json_reply and _reports_error(bytes(captured)):
                    ok = False
                record("ok" if ok else "error")

        return ProxyReply(reply.status_code, returned, stream())


def _tool_names(body: bytes) -> list[str] | None:
    """The tool of each request: `tools/call` names its tool, any other method names itself."""
    try:
        message: Any = json.loads(body)
    except ValueError:
        return None
    items: list[object] = cast(list[object], message) if isinstance(message, list) else [message]
    names: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        fields = cast(dict[str, object], item)
        method = fields.get("method")
        if not isinstance(method, str):
            return None
        params = fields.get("params")
        params_map = cast(dict[str, object], params) if isinstance(params, dict) else {}
        tool = params_map.get("name")
        names.append(tool if method == "tools/call" and isinstance(tool, str) else method)
    return names or None


def _reports_error(captured: bytes) -> bool:
    """A JSON-RPC error, or a tool result that says `isError`, in an HTTP 200 reply."""
    try:
        message: Any = json.loads(captured)
    except ValueError:
        return False
    items: list[object] = cast(list[object], message) if isinstance(message, list) else [message]
    for item in items:
        if not isinstance(item, dict):
            continue
        fields = cast(dict[str, object], item)
        result = fields.get("result")
        result_map = cast(dict[str, object], result) if isinstance(result, dict) else {}
        if "error" in fields or result_map.get("isError") is True:
            return True
    return False


def _error(status: int, kind: str, message: str) -> ProxyReply:
    payload = json.dumps({"error": {"type": kind, "message": message}}).encode()

    async def body() -> AsyncIterator[bytes]:
        yield payload

    return ProxyReply(status, {"content-type": "application/json"}, body())


class UrllibUpstream:
    """Standard-library upstream: one blocking POST in a thread, reply buffered."""

    def __init__(self, *, timeout: float = 60.0) -> None:
        self._timeout = timeout

    async def post(self, url: str, *, body: bytes, headers: Mapping[str, str]) -> UpstreamReply:
        status, reply_headers, data = await asyncio.to_thread(self._post, url, body, dict(headers))

        async def chunks() -> AsyncIterator[bytes]:
            yield data

        return UpstreamReply(status, reply_headers, chunks())

    def _post(
        self, url: str, body: bytes, headers: dict[str, str]
    ) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=self._timeout) as response:
                return response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers.items()), error.read()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the daemon's auth header to another host; return it as a reply."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        return None

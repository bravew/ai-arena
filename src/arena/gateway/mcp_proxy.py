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
import contextlib
import ipaddress
import json
import re
import socket
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
_MAX_REPLY = 8 * 1024 * 1024
_ALLOWED_METHODS = frozenset({"POST", "GET", "DELETE"})


class McpConfigError(ValueError):
    """The kit's MCP servers cannot be served: an unset variable or an unusable URL."""


class McpReplyTooLarge(RuntimeError):
    """An upstream MCP reply exceeded the proxy's bounded response size."""


class McpDestinationError(ValueError):
    """A configured MCP host resolves to an address the daemon must not contact."""


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
    async def request(
        self, method: str, url: str, *, body: bytes | None, headers: Mapping[str, str]
    ) -> UpstreamReply: ...


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


def resolve_routes(
    servers: Sequence[McpServer],
    env: Mapping[str, str],
    *,
    allowed_hosts: frozenset[str],
) -> dict[str, _Route]:
    """Resolve secrets and reject private IP literals; optionally pin approved hostnames.

    Production should pass administrator-approved hostnames. The standard default refuses
    private and reserved IP literals. Hostnames are resolved and checked again on every request
    by `UrllibUpstream` to reduce DNS rebinding risk.
    """
    routes: dict[str, _Route] = {}
    missing: set[str] = set()
    for server in servers:
        if server.name in routes:
            raise McpConfigError(f"duplicate MCP server name {server.name!r}")
        if not server.url:
            continue
        parts = urlsplit(server.url)
        host = (parts.hostname or "").lower().rstrip(".")
        if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
            raise McpConfigError(f"mcp {server.name}: url must be http(s) without credentials")
        try:
            ip = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            ip = None
        if not allowed_hosts or host not in allowed_hosts:
            raise McpDestinationError(f"mcp {server.name}: host {host!r} is not allowlisted")
        if ip is not None and (not ip.is_global or ip.is_reserved):
            raise McpDestinationError(f"mcp {server.name}: private or reserved IP destination")
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
        allowed_hosts: frozenset[str],
    ) -> None:
        self._routes = resolve_routes(servers, env, allowed_hosts=allowed_hosts)
        self._upstream = upstream
        self._spans = spans

    def names(self) -> tuple[str, ...]:
        return tuple(self._routes)

    async def handle(
        self,
        name: str,
        method: str,
        body: bytes,
        headers: Mapping[str, str],
        *,
        trial_id: str,
    ) -> ProxyReply:
        """Forward one MCP POST, GET event stream or DELETE session request.

        The gateway route must authenticate the trial token before calling this method.
        """
        route = self._routes.get(name)
        if route is None:
            return _error(404, "unknown_mcp_server", "no such MCP server in this kit")
        method = method.upper()
        if method not in {"POST", "GET", "DELETE"}:
            return _error(405, "method_not_allowed", "MCP supports POST, GET and DELETE")
        if method == "POST" and len(body) > _MAX_REQUEST:
            return _error(413, "request_too_large", "MCP request body is too large")
        tools = _tool_names(body) if method == "POST" else [method.lower()]
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
            reply = await self._upstream.request(
                method, route.url, body=body if method == "POST" else None, headers=forwarded
            )
        except Exception:
            record("error")
            return _error(502, "mcp_upstream_error", "MCP server request failed")
        returned = {k: v for k, v in reply.headers.items() if k.lower() in _RETURNED_REPLY}

        async def stream() -> AsyncIterator[bytes]:
            captured = bytearray()
            total = 0
            ok = 200 <= reply.status_code < 300
            try:
                async for chunk in reply.body:
                    if total + len(chunk) > _MAX_REPLY:
                        ok = False
                        raise McpReplyTooLarge(f"MCP reply exceeded {_MAX_REPLY} bytes")
                    total += len(chunk)
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

    def __init__(
        self,
        *,
        timeout: float = 60.0,
        allowed_hosts: frozenset[str],
    ) -> None:
        self._timeout = timeout
        self._allowed_hosts = frozenset(host.lower().rstrip(".") for host in allowed_hosts)
        if not self._allowed_hosts:
            raise ValueError("UrllibUpstream requires an administrator MCP host allowlist")

    async def request(
        self, method: str, url: str, *, body: bytes | None, headers: Mapping[str, str]
    ) -> UpstreamReply:
        status, reply_headers, data = await asyncio.to_thread(
            self._request, method, url, body, dict(headers)
        )

        async def chunks() -> AsyncIterator[bytes]:
            yield data

        return UpstreamReply(status, reply_headers, chunks())

    def _request(
        self, method: str, url: str, body: bytes | None, headers: dict[str, str]
    ) -> tuple[int, dict[str, str], bytes]:
        if method not in _ALLOWED_METHODS:
            raise McpConfigError(f"unsupported MCP method {method!r}")
        self._check_destination(url)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=self._timeout) as response:
                return response.status, dict(response.headers.items()), _bounded_read(response)
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers.items()), _bounded_read(error)

    def _check_destination(self, url: str) -> None:
        host = urlsplit(url).hostname
        if host is None:
            raise McpDestinationError("MCP URL has no host")
        normalized = host.lower().rstrip(".")
        if normalized not in self._allowed_hosts:
            raise McpDestinationError(f"host {normalized!r} is not allowlisted")
        try:
            addresses = socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
        except OSError as error:
            raise McpDestinationError("MCP host could not be resolved") from error
        if not addresses:
            raise McpDestinationError("MCP host has no addresses")
        explicitly_allowed_ip = False
        with contextlib.suppress(ValueError):
            explicitly_allowed_ip = ipaddress.ip_address(normalized).is_global
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if (not ip.is_global or ip.is_reserved) and not explicitly_allowed_ip:
                raise McpDestinationError("MCP host resolves to a private or reserved address")


def _bounded_read(response: Any) -> bytes:
    data = response.read(_MAX_REPLY + 1)
    if len(data) > _MAX_REPLY:
        raise McpReplyTooLarge(f"MCP reply exceeded {_MAX_REPLY} bytes")
    return data


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the daemon's auth header to another host; return it as a reply."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        return None

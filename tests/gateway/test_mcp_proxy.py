from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest

from arena.core.models import McpServer
from arena.gateway.mcp_proxy import McpConfigError, McpProxy, McpSpan, UpstreamReply

CALL = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "query", "arguments": {}},
    }
).encode()
OK = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": []}}).encode()
DOCS = McpServer(
    name="docs",
    url="https://upstream.invalid/mcp",
    headers={"Authorization": "Bearer ${DOCS_TOKEN}"},
)
ENV = {"DOCS_TOKEN": "fixture-daemon-header-28"}


class FakeUpstream:
    def __init__(self, status: int = 200, body: bytes = OK, *, fail: bool = False) -> None:
        self.status, self.body, self.fail = status, body, fail
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []

    async def request(
        self, method: str, url: str, *, body: bytes | None, headers: Mapping[str, str]
    ) -> UpstreamReply:
        self.requests.append((method, url, body or b"", dict(headers)))
        if self.fail:
            raise ConnectionError("upstream unavailable")

        async def chunks() -> AsyncIterator[bytes]:
            yield self.body

        content_type = "text/event-stream" if self.status == 302 else "application/json"
        return UpstreamReply(
            self.status, {"content-type": content_type, "set-cookie": "fixture=1"}, chunks()
        )


async def _call(
    proxy: McpProxy,
    name: str = "docs",
    body: bytes = CALL,
    headers: Mapping[str, str] | None = None,
    method: str = "POST",
) -> tuple[int, bytes, Mapping[str, str]]:
    reply = await proxy.handle(name, method, body, headers or {}, trial_id="t1")
    return reply.status_code, b"".join([chunk async for chunk in reply.body]), reply.headers


def _proxy(upstream: Any, spans: list[McpSpan], servers: list[McpServer] | None = None) -> McpProxy:
    return McpProxy(
        servers or [DOCS],
        env=ENV,
        upstream=upstream,
        spans=spans.append,
        allowed_hosts=frozenset({"upstream.invalid"}),
    )


def test_upstream_gets_daemon_header_and_never_the_container_token() -> None:
    upstream, spans = FakeUpstream(), []

    status, body, headers = asyncio.run(
        _call(
            _proxy(upstream, spans),
            headers={
                "Authorization": "Bearer arena-trial-fixture",
                "Cookie": "fixture=1",
                "Content-Type": "application/json",
                "Mcp-Session-Id": "fixture-session",
            },
        )
    )

    method, url, sent_body, sent = upstream.requests[0]
    assert (status, body) == (200, OK) and url == "https://upstream.invalid/mcp"
    assert method == "POST"
    assert sent_body == CALL
    assert sent["authorization"] == f"Bearer {ENV['DOCS_TOKEN']}"
    assert "arena-trial-fixture" not in json.dumps(sent) and "cookie" not in sent
    assert sent["mcp-session-id"] == "fixture-session"
    assert "set-cookie" not in {key.lower() for key in headers}


def test_each_request_becomes_one_span_with_server_tool_duration_status() -> None:
    spans: list[McpSpan] = []

    asyncio.run(_call(_proxy(FakeUpstream(), spans)))
    asyncio.run(
        _call(_proxy(FakeUpstream(), spans), body=json.dumps({"method": "tools/list"}).encode())
    )

    assert [(span.server, span.tool, span.status, span.trial_id) for span in spans] == [
        ("docs", "query", "ok", "t1"),
        ("docs", "tools/list", "ok", "t1"),
    ]
    assert all(span.duration_ms >= 0 for span in spans)


def test_a_batch_records_one_span_per_request() -> None:
    spans: list[McpSpan] = []
    batch = json.dumps([json.loads(CALL), {"method": "ping"}]).encode()

    asyncio.run(_call(_proxy(FakeUpstream(), spans), body=batch))

    assert [span.tool for span in spans] == ["query", "ping"]


@pytest.mark.parametrize(
    ("upstream", "reply_status"),
    [
        (FakeUpstream(500, b"{}"), 500),
        (FakeUpstream(200, json.dumps({"id": 1, "error": {"code": -1}}).encode()), 200),
        (FakeUpstream(200, json.dumps({"id": 1, "result": {"isError": True}}).encode()), 200),
    ],
)
def test_failed_requests_are_error_spans(upstream: FakeUpstream, reply_status: int) -> None:
    spans: list[McpSpan] = []

    status, _, _ = asyncio.run(_call(_proxy(upstream, spans)))

    assert status == reply_status and [span.status for span in spans] == ["error"]


def test_unreachable_upstream_returns_a_nonrevealing_502() -> None:
    spans: list[McpSpan] = []

    status, body, _ = asyncio.run(_call(_proxy(FakeUpstream(fail=True), spans)))

    assert status == 502
    assert b"upstream.invalid" not in body and ENV["DOCS_TOKEN"].encode() not in body
    assert [span.status for span in spans] == ["error"]


def test_only_kit_servers_are_reachable_and_bodies_must_be_json_rpc() -> None:
    upstream, spans = FakeUpstream(), []
    proxy = _proxy(upstream, spans)

    assert asyncio.run(_call(proxy, name="other"))[0] == 404
    assert asyncio.run(_call(proxy, body=b"not json"))[0] == 400
    assert asyncio.run(_call(proxy, body=b'{"no":"method"}'))[0] == 400
    assert asyncio.run(_call(proxy, body=b"x" * (5 * 1024 * 1024)))[0] == 413
    assert upstream.requests == [] and spans == []


def test_command_servers_are_not_routed_and_unset_env_names_fail_preflight() -> None:
    command = McpServer(name="local", command="fixture-server")
    assert _proxy(FakeUpstream(), [], [command, DOCS]).names() == ("docs",)

    with pytest.raises(McpConfigError, match=r"\$\{DOCS_TOKEN\}"):
        McpProxy(
            [DOCS], env={}, upstream=FakeUpstream(), allowed_hosts=frozenset({"upstream.invalid"})
        )
    with pytest.raises(McpConfigError, match="http"):
        McpProxy(
            [McpServer(name="fixture", url="file:///etc/hosts")],
            env={},
            upstream=FakeUpstream(),
            allowed_hosts=frozenset(),
        )
    duplicate = McpServer(name="docs", url="https://other.example/mcp")
    with pytest.raises(McpConfigError, match="duplicate MCP server name"):
        McpProxy(
            [DOCS, duplicate],
            env=ENV,
            upstream=FakeUpstream(),
            allowed_hosts=frozenset({"upstream.invalid", "other.example"}),
        )


class _HttpResponse:
    def __init__(self, status: int, headers: Mapping[str, str], body: bytes) -> None:
        self.status = status
        self.headers = dict(headers)
        self.body = body
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]

    def close(self) -> None:
        self.closed = True


class LocalHttpUpstream:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []

    async def request(
        self, method: str, url: str, *, body: bytes | None, headers: Mapping[str, str]
    ) -> UpstreamReply:
        self.requests.append((method, url, body, dict(headers)))
        if url.endswith("/redirect"):
            status, response_headers, payload = 302, {"Location": "http://127.0.0.1:1/next"}, b""
        elif method == "GET":
            status, response_headers, payload = (
                200,
                {"content-type": "text/event-stream"},
                b"event: ping\\ndata: {}\\n\\n",
            )
        elif method == "DELETE":
            status, response_headers, payload = 200, {}, b""
        else:
            status, response_headers, payload = 200, {"content-type": "application/json"}, OK
        response = _HttpResponse(status, response_headers, payload)

        async def chunks() -> AsyncIterator[bytes]:
            yield response.read()
            response.close()

        return UpstreamReply(status, response_headers, chunks())


def test_http_transport_handles_auth_methods_and_redirect_responses() -> None:
    spans: list[McpSpan] = []
    upstream = LocalHttpUpstream()
    proxy = McpProxy(
        [
            McpServer(
                name="docs",
                url="https://docs.example/mcp",
                headers={"Authorization": "Bearer ${T}"},
            ),
            McpServer(
                name="moved",
                url="https://docs.example/redirect",
                headers={"Authorization": "Bearer ${T}"},
            ),
        ],
        env={"T": "fixture-header-value"},
        upstream=upstream,
        spans=spans.append,
        allowed_hosts=frozenset({"docs.example"}),
    )

    ok = asyncio.run(
        _call(
            proxy,
            headers={
                "Authorization": "Bearer arena-trial-fixture",
                "Mcp-Session-Id": "fixture-session",
            },
        )
    )
    moved = asyncio.run(_call(proxy, name="moved"))
    event = asyncio.run(
        _call(proxy, method="GET", body=b"", headers={"Mcp-Session-Id": "fixture-session"})
    )
    closed = asyncio.run(
        _call(proxy, method="DELETE", body=b"", headers={"Mcp-Session-Id": "fixture-session"})
    )

    assert ok[0] == 200 and ok[1] == OK
    assert moved[0] == 302  # returned to caller, never followed with daemon auth
    assert event[0] == 200 and event[2].get("content-type") == "text/event-stream"
    assert closed[0] == 200
    assert len(upstream.requests) == 4
    assert upstream.requests[0][3]["authorization"] == "Bearer fixture-header-value"
    assert upstream.requests[0][3]["mcp-session-id"] == "fixture-session"
    assert all(
        request[3].get("mcp-session-id") == "fixture-session"
        for request in upstream.requests
        if request[0] in {"GET", "DELETE"}
    )
    assert [span.status for span in spans] == ["ok", "error", "ok", "ok"]

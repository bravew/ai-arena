"""`/mcp/{name}` on the gateway: trial-only, token never forwarded, spans recorded."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from starlette.testclient import TestClient

from arena.catalog.config import load_catalog
from arena.core.models import McpServer
from arena.gateway.auth import OPS_TOKEN, token_for_judge, token_for_trial
from arena.gateway.mcp_proxy import McpProxy, McpSpan, UpstreamReply
from arena.gateway.server import create_app
from arena.providers.config import load_providers

ROOT = Path(__file__).resolve().parents[2]
CALL = json.dumps(
    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "query"}}
).encode()
OK = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": []}}).encode()


class FakeUpstream:
    def __init__(self) -> None:
        self.requests: list[tuple[str, bytes, dict[str, str]]] = []

    async def request(
        self, method: str, url: str, *, body: bytes | None, headers: Mapping[str, str]
    ) -> UpstreamReply:
        self.requests.append((method, body or b"", dict(headers)))

        async def chunks() -> AsyncIterator[bytes]:
            yield OK

        return UpstreamReply(200, {"content-type": "application/json"}, chunks())


def _client(upstream: FakeUpstream, spans: list[McpSpan], *, proxy: bool = True) -> TestClient:
    mcp = (
        McpProxy(
            [
                McpServer(
                    name="docs", url="https://upstream.invalid/mcp", headers={"X-Key": "daemon"}
                )
            ],
            env={},
            upstream=upstream,
            spans=spans.append,
            allowed_hosts=frozenset({"upstream.invalid"}),
        )
        if proxy
        else None
    )
    app = create_app(
        load_providers(ROOT / "providers.yaml"),
        load_catalog(ROOT / "catalog" / "models.yaml"),
        mcp=mcp,
    )
    return TestClient(app)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_trial_call_is_proxied_without_its_token_and_recorded() -> None:
    upstream, spans = FakeUpstream(), []
    response = _client(upstream, spans).post(
        "/mcp/docs",
        content=CALL,
        headers={**_auth(token_for_trial("t1")), "content-type": "application/json"},
    )
    assert response.status_code == 200
    assert response.content == OK
    (method, body, sent) = upstream.requests[0]
    assert (method, body) == ("POST", CALL)
    assert sent["x-key"] == "daemon"
    assert not any("arena-" in value for value in sent.values())
    assert [(s.trial_id, s.server, s.tool, s.status) for s in spans] == [
        ("t1", "docs", "query", "ok")
    ]


def test_only_trials_may_call_mcp() -> None:
    upstream = FakeUpstream()
    client = _client(upstream, [])
    for token in (OPS_TOKEN, token_for_judge("run1")):
        assert client.post("/mcp/docs", content=CALL, headers=_auth(token)).status_code == 403
    assert client.post("/mcp/docs", content=CALL).status_code == 401
    assert upstream.requests == []


def test_unknown_server_and_unconfigured_proxy() -> None:
    headers = _auth(token_for_trial("t1"))
    assert (
        _client(FakeUpstream(), []).post("/mcp/nope", content=CALL, headers=headers).status_code
        == 404
    )
    assert (
        _client(FakeUpstream(), [], proxy=False)
        .post("/mcp/docs", content=CALL, headers=headers)
        .status_code
        == 404
    )

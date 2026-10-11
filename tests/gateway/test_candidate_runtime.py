"""Candidate planning, rests and next-key retry through the request path (DEV_PLAN 5.4-5.5)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from arena.catalog.config import load_catalog
from arena.core.models import Call
from arena.gateway.classify import FailureClass
from arena.gateway.endpoints import EndpointResolver
from arena.gateway.lanes import LanePool
from arena.gateway.litellm_adapter import NativeDispatcher
from arena.gateway.rests import RestBook
from arena.gateway.server import CallContext, DispatchContext, UpstreamResponse, create_app
from arena.gateway.transport import HttpTransport
from arena.providers.config import load_providers

HEADERS = {"Authorization": "Bearer arena-trial-1"}
REQUEST = {"model": "p/m", "messages": []}
OK = (200, b'{"id":"ok","model":"m","usage":{"input_tokens":1,"output_tokens":1}}', {})


def _rate_limit(retry_after: str = "30") -> tuple[int, bytes, dict[str, str]]:
    return 429, b'{"error":{"message":"rate limit exceeded"}}', {"retry-after": retry_after}


def _failure(status: int, message: str) -> tuple[int, bytes, dict[str, str]]:
    return status, json.dumps({"error": {"message": message}}).encode(), {}


class MemoryLedger:
    def __init__(self) -> None:
        self.calls: list[Call] = []

    def record(self, call: Call) -> Call:
        self.calls.append(call)
        return call


@dataclass
class Script:
    """Scripted per-key replies; each key consumes its list in order."""

    replies: dict[str, list[Any]]
    order: list[str] = field(default_factory=list)

    async def dispatch(self, context: DispatchContext) -> UpstreamResponse:
        assert context.candidate is not None
        key = context.candidate.id
        self.order.append(key)
        reply = self.replies[key].pop(0)
        if isinstance(reply, Exception):
            raise reply
        status, body, headers = reply

        async def chunks() -> AsyncIterator[bytes]:
            yield body

        return UpstreamResponse(
            status, {"content-type": "application/json", **headers}, chunks(), None, "m"
        )


def _setup(tmp_path: Path, *, key_routing: str = "order") -> tuple[Any, Any]:
    (tmp_path / "providers.yaml").write_text(
        "providers:\n"
        "  - id: p\n"
        "    apis: {anthropic: https://example.invalid}\n"
        "    keys: [{id: a, env: KEY_A}, {id: b, env: KEY_B}]\n"
        f"    key_routing: {key_routing}\n",
        encoding="utf-8",
    )
    (tmp_path / "models.yaml").write_text(
        "price_version: test\nmodels:\n"
        "  - ref: p/m\n    protocols: [anthropic]\n    pricing: subscription\n",
        encoding="utf-8",
    )
    return load_providers(tmp_path / "providers.yaml"), load_catalog(tmp_path / "models.yaml")


def _run(
    tmp_path: Path,
    script: Script,
    *,
    rests: RestBook | None = None,
    lanes: LanePool | None = None,
    ledger: MemoryLedger | None = None,
    key_routing: str = "order",
    requests: int = 1,
) -> list[Any]:
    providers, catalog = _setup(tmp_path, key_routing=key_routing)
    app = create_app(
        providers,
        catalog,
        dispatcher=script.dispatch,
        translator=lambda source, target, payload: payload,
        rests=rests,
        lanes=lanes,
        ledger=ledger,
        context_provider=(
            (lambda target, caller: CallContext("run-1", "acct", "call-1", 1, None, "test"))
            if ledger is not None
            else None
        ),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        return [client.post("/v1/messages", headers=HEADERS, json=REQUEST) for _ in range(requests)]


def test_rate_limited_key_rests_and_the_next_key_answers(tmp_path: Path) -> None:
    rests, lanes, ledger = RestBook(), LanePool(concurrency=1), MemoryLedger()
    script = Script({"a": [_rate_limit()], "b": [OK]})

    (response,) = _run(tmp_path, script, rests=rests, lanes=lanes, ledger=ledger)

    assert response.status_code == 200
    assert script.order == ["a", "b"]
    assert rests.is_resting("p/a") and not rests.is_resting("p/b")
    assert lanes.for_key("p/a").in_flight == 0 and lanes.for_key("p/b").in_flight == 0
    (call,) = ledger.calls
    assert [(t.account_id, t.status, t.error_class) for t in call.tries] == [
        ("a", 429, "rate_limit"),
        ("b", 200, None),
    ]
    assert call.tries[0].rest_ms > 0
    assert call.error_class is None


def test_resting_key_is_planned_last_but_never_dropped(tmp_path: Path) -> None:
    rests = RestBook()
    rests.rest("p/a", FailureClass.RATE_LIMIT, "600")
    script = Script({"a": [OK], "b": [_rate_limit()]})

    (response,) = _run(tmp_path, script, rests=rests)

    # b is tried first, rate limits, and the resting a is still used as the last candidate.
    assert response.status_code == 200
    assert script.order == ["b", "a"]
    assert not rests.is_resting("p/a")  # a success clears the rest


def test_quota_is_not_reported_as_rate_limit(tmp_path: Path) -> None:
    rests, ledger = RestBook(), MemoryLedger()
    script = Script({"a": [_failure(429, "insufficient_quota")], "b": [OK]})

    _run(tmp_path, script, rests=rests, ledger=ledger)

    assert [t.error_class for t in ledger.calls[0].tries] == ["quota", None]
    assert script.order == ["a", "b"]


def test_model_refused_rests_that_model_on_that_key_only(tmp_path: Path) -> None:
    rests = RestBook()
    script = Script({"a": [_failure(404, "model not found")], "b": [OK]})

    (response,) = _run(tmp_path, script, rests=rests)

    assert response.status_code == 200
    assert rests.is_resting("p/a:p/m")
    assert not rests.is_resting("p/a")


@pytest.mark.parametrize(
    ("status", "message", "failure"),
    [
        (401, "invalid api key", "auth"),
        (429, "weekly usage limit reached", "subscription_limit"),
        (400, "content_filter", "content_refusal"),
    ],
)
def test_classes_that_another_key_cannot_fix_are_not_retried(
    tmp_path: Path, status: int, message: str, failure: str
) -> None:
    rests, ledger = RestBook(), MemoryLedger()
    script = Script({"a": [_failure(status, message)], "b": [OK]})

    (response,) = _run(tmp_path, script, rests=rests, ledger=ledger)

    assert response.status_code == status
    assert script.order == ["a"]
    assert ledger.calls[0].error_class == failure
    assert rests.is_resting("p/a") is (failure != "content_refusal")


def test_every_key_failing_returns_the_last_failure_and_frees_all_lanes(tmp_path: Path) -> None:
    lanes = LanePool(concurrency=1)
    script = Script({"a": [_failure(503, "overloaded")], "b": [_failure(502, "bad gateway")]})

    (response,) = _run(tmp_path, script, lanes=lanes)

    assert response.status_code == 502
    assert response.json()["error"]["type"] == "upstream_error"
    assert script.order == ["a", "b"]
    assert lanes.for_key("p/a").in_flight == 0 and lanes.for_key("p/b").in_flight == 0


def test_timeout_before_a_response_moves_to_the_next_key(tmp_path: Path) -> None:
    rests, ledger = RestBook(), MemoryLedger()
    script = Script({"a": [httpx.ReadTimeout("slow")], "b": [OK]})

    (response,) = _run(tmp_path, script, rests=rests, ledger=ledger)

    assert response.status_code == 200
    assert rests.is_resting("p/a")
    assert [t.error_class for t in ledger.calls[0].tries] == ["timeout", None]
    assert ledger.calls[0].tries[0].status is None


def test_timeout_on_the_last_key_is_an_upstream_error(tmp_path: Path) -> None:
    rests = RestBook()
    script = Script({"a": [httpx.ReadTimeout("slow")], "b": [httpx.ReadTimeout("slow")]})

    (response,) = _run(tmp_path, script, rests=rests)

    assert response.status_code == 502
    assert script.order == ["a", "b"]
    assert rests.is_resting("p/a") and rests.is_resting("p/b")


def test_a_started_stream_is_never_retried(tmp_path: Path) -> None:
    order: list[str] = []

    async def dispatch(context: DispatchContext) -> UpstreamResponse:
        assert context.candidate is not None
        order.append(context.candidate.id)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"data: first\n\n"
            raise ConnectionError("dropped mid-stream")

        return UpstreamResponse(200, {"content-type": "text/event-stream"}, chunks())

    providers, catalog = _setup(tmp_path)
    app = create_app(providers, catalog, dispatcher=dispatch, translator=lambda s, t, p: p)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/v1/messages", headers=HEADERS, json={**REQUEST, "stream": True})

    # The response had started, so key b is never asked for the rest of the reply.
    assert response.status_code == 200
    assert order == ["a"]


def test_unsupported_key_routing_fails_closed(tmp_path: Path) -> None:
    script = Script({"a": [OK], "b": [OK]})

    (response,) = _run(tmp_path, script, key_routing="rotate")

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "key_routing_unsupported"
    assert script.order == []


def test_selected_key_credentials_reach_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KEY_A", "secret-a")
    monkeypatch.setenv("KEY_B", "secret-b")
    sent: list[str] = []
    replies = [_rate_limit(), OK]

    async def fake_request(url: str, *, body: bytes, headers: Mapping[str, str]) -> Any:
        sent.append(headers["x-api-key"])
        status, payload, extra = replies.pop(0)

        async def chunks() -> AsyncIterator[bytes]:
            yield payload

        return await HttpTransport().from_response(
            status, {"content-type": "application/json", **extra}, chunks()
        )

    dispatcher = NativeDispatcher(endpoint_resolver=EndpointResolver())
    monkeypatch.setattr(dispatcher.transport, "request", fake_request)
    providers, catalog = _setup(tmp_path)
    app = create_app(providers, catalog, dispatcher=dispatcher.dispatch)
    with TestClient(app) as client:
        response = client.post("/v1/messages", headers=HEADERS, json=REQUEST)

    assert response.status_code == 200
    assert sent == ["secret-a", "secret-b"]

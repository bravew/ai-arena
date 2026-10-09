"""Gateway failure classification, candidate planning and rests."""

from datetime import UTC, datetime, timedelta

import pytest

from arena.gateway.classify import (
    FailureClass,
    SwappedModelError,
    classify_failure,
    classify_reply,
    enforce_served_model,
)
from arena.gateway.plan import Candidate, plan_candidates
from arena.gateway.rests import RestBook


@pytest.mark.parametrize(
    ("status", "body", "expected", "neighbor_body", "neighbor"),
    [
        (
            429,
            b'{"error":{"type":"rate_limit_error","message":"Too many requests per minute"}}',
            FailureClass.RATE_LIMIT,
            b'{"error":{"message":"quota exceeded"}}',
            FailureClass.QUOTA,
        ),
        (
            429,
            b'{"error":{"type":"insufficient_quota","message":"You exceeded your current quota"}}',
            FailureClass.QUOTA,
            b'{"error":{"type":"rate_limit_error","message":"Too many requests per minute"}}',
            FailureClass.RATE_LIMIT,
        ),
        (
            401,
            b'{"error":{"message":"invalid API key"}}',
            FailureClass.AUTH,
            b'{"error":{"message":"OAuth session expired and could not be refreshed"}}',
            FailureClass.AUTH_REFRESH,
        ),
        (
            401,
            b'{"error":{"message":"OAuth session expired and could not be refreshed"}}',
            FailureClass.AUTH_REFRESH,
            b'{"error":{"message":"invalid API key"}}',
            FailureClass.AUTH,
        ),
        (
            404,
            b'{"error":{"code":"model_not_found","message":"unknown model"}}',
            FailureClass.MODEL_REFUSED,
            b'{"error":{"message":"resource not found"}}',
            None,
        ),
        (
            503,
            b'{"error":{"message":"upstream unavailable"}}',
            FailureClass.UPSTREAM,
            b'{"error":{"message":"upstream timeout"}}',
            FailureClass.UPSTREAM,
        ),
        (
            400,
            b'{"error":{"code":"content_policy_violation","message":"refused"}}',
            FailureClass.CONTENT_REFUSAL,
            b'{"error":{"code":"model_not_found","message":"unknown model"}}',
            FailureClass.MODEL_REFUSED,
        ),
    ],
)
def test_classification_is_narrow(
    status: int, body: bytes, expected: FailureClass, neighbor_body: bytes, neighbor: FailureClass
) -> None:
    assert classify_failure(status, body) is expected
    assert classify_failure(status, neighbor_body) is neighbor


def test_rate_limited_key_rests_and_next_candidate_is_usable() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    book = RestBook()
    rest = book.rest("key-a", FailureClass.RATE_LIMIT, "120", now=now)
    assert rest is not None
    assert rest.until == now + timedelta(seconds=120)
    assert book.is_resting("key-a", now=now)
    assert not book.is_resting("key-b", now=now)


def test_quota_429_does_not_get_a_rate_limit_rest() -> None:
    book = RestBook()
    failure = classify_failure(
        429, b'{"error":{"type":"insufficient_quota","message":"quota exceeded"}}'
    )
    assert failure is FailureClass.QUOTA
    assert book.rest("key-a", failure) is not None
    assert book.rest("key-b", FailureClass.RATE_LIMIT, "5").failure is FailureClass.RATE_LIMIT  # type: ignore[union-attr]


def test_planner_keeps_only_requested_model_and_puts_resting_key_last() -> None:
    candidates = (
        Candidate("resting", "vendor/model", True),
        Candidate("other-model", "vendor/other"),
        Candidate("ready", "vendor/model"),
    )
    assert tuple(candidate.key for candidate in plan_candidates(candidates, "vendor/model")) == (
        "ready",
        "resting",
    )


def test_html_reply_is_bad_reply_and_model_is_reported() -> None:
    assert (
        classify_reply(200, "text/html", b"<html>sign in</html>").failure is FailureClass.BAD_REPLY
    )
    result = classify_reply(
        200, "application/json", b'{"model":"vendor/other"}', model_asked="vendor/model"
    )
    assert result.swapped
    assert result.failure is None
    enforce_served_model(result, require_served_model=False)
    with pytest.raises(SwappedModelError):
        enforce_served_model(result, require_served_model=True)


def test_stream_failure_after_first_byte_is_not_retried() -> None:
    async def scenario() -> None:
        from arena.gateway.relay import relay_stream

        calls = 0

        async def attempt():
            nonlocal calls
            calls += 1

            async def stream():
                yield b"first"
                raise ConnectionError("after first byte")

            return stream()

        iterator = relay_stream((attempt, attempt))
        assert await anext(iterator) == b"first"
        with pytest.raises(ConnectionError):
            await anext(iterator)
        assert calls == 1

    import asyncio

    asyncio.run(scenario())


def test_content_refusal_is_not_rested() -> None:
    assert (
        classify_reply(200, "application/json", b'{"stop_reason":"refusal"}').failure
        is FailureClass.CONTENT_REFUSAL
    )
    assert RestBook().rest("key-a", FailureClass.CONTENT_REFUSAL) is None

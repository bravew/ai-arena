"""Per-key rests applied after classified upstream failures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from threading import RLock

from arena.gateway.classify import FailureClass

_DEFAULT_RATE_LIMIT = timedelta(seconds=60)
_DEFAULT_QUOTA = timedelta(minutes=30)
_DEFAULT_FAILURE = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class Rest:
    """A key's exclusion interval and the failure that caused it."""

    key: str
    failure: FailureClass
    until: datetime


class RestBook:
    """Thread-safe in-memory key rests with Retry-After handling."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._rests: dict[str, Rest] = {}
        self._rate_failures: dict[str, int] = {}

    def rest(
        self,
        key: str,
        failure: FailureClass,
        retry_after: str | None = None,
        *,
        now: datetime | None = None,
    ) -> Rest | None:
        """Rest a retryable key; content refusals are returned without a rest."""
        if failure is FailureClass.CONTENT_REFUSAL:
            return None
        current = now or datetime.now(UTC)
        duration = _duration(failure, retry_after, current)
        with self._lock:
            if failure is FailureClass.RATE_LIMIT:
                count = self._rate_failures.get(key, 0) + 1
                self._rate_failures[key] = count
                duration = max(duration, _DEFAULT_RATE_LIMIT * min(2 ** (count - 1), 32))
            result = Rest(key, failure, current + duration)
            self._rests[key] = result
            return result

    def is_resting(self, key: str, *, now: datetime | None = None) -> bool:
        """Return whether a key remains rested at `now`."""
        current = now or datetime.now(UTC)
        with self._lock:
            rest = self._rests.get(key)
            if rest is None:
                return False
            if rest.until <= current:
                del self._rests[key]
                return False
            return True

    def clear(self, key: str) -> None:
        """Clear a key's rest after a successful reply."""
        with self._lock:
            self._rests.pop(key, None)
            self._rate_failures.pop(key, None)


def _duration(failure: FailureClass, retry_after: str | None, now: datetime) -> timedelta:
    if failure is FailureClass.RATE_LIMIT and retry_after:
        try:
            return max(timedelta(seconds=1), timedelta(seconds=float(retry_after)))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(retry_after)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return max(timedelta(seconds=1), parsed - now)
            except (TypeError, ValueError, OverflowError):
                pass
    if failure in (FailureClass.QUOTA, FailureClass.CREDIT, FailureClass.SUBSCRIPTION_LIMIT):
        return _DEFAULT_QUOTA
    return _DEFAULT_FAILURE

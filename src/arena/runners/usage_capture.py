"""Turn a `local-cli` trial result into a ledger row, an event and a budget entry.

This is the counterpart of the reached check for a trial the gateway never saw. A trial counts
as reached only when the CLI exited cleanly, reported usage, and its served model was verified
to be the requested one. Anything less is recorded as a failed call and is not a success.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from arena.catalog.config import ModelCatalog
from arena.core.models import Call, ErrorClass, Tokens
from arena.gateway.budget import Budget
from arena.obs.cli_usage import build_cli_call, call_finished_event
from arena.obs.events import EventLog
from arena.obs.ledger import CallsLedger
from arena.runners.local_cli import FailureKind, LocalCliResult

# FailureKind -> (HTTP-like status, error class). A spawn failure never made a model request.
_FAILURE: dict[FailureKind, tuple[int | None, ErrorClass]] = {
    FailureKind.LIMIT_REACHED: (429, "subscription_limit"),
    FailureKind.AUTH_REQUIRED: (401, "auth"),
    FailureKind.TIMEOUT: (None, "timeout"),
    FailureKind.MODEL_MISMATCH: (200, "model_refused"),
    FailureKind.EXIT_CODE: (500, "upstream"),
    FailureKind.PROTOCOL: (500, "bad_reply"),
    FailureKind.NO_USAGE: (500, "bad_reply"),
}


class CaptureError(Exception):
    pass


@dataclass(frozen=True)
class CaptureOutcome:
    reached: bool
    reason: str | None
    call: Call | None


class UsageCapture:
    def __init__(
        self,
        ledger: CallsLedger,
        events: EventLog,
        budget: Budget,
        catalog: ModelCatalog,
        next_seq: Callable[[], int],
    ) -> None:
        self._ledger = ledger
        self._events = events
        self._budget = budget
        self._catalog = catalog
        self._next_seq = next_seq

    async def capture(
        self,
        *,
        run_id: str,
        trial_id: str,
        model_ref: str,
        runtime: str,
        account_id: str,
        result: LocalCliResult,
    ) -> CaptureOutcome:
        model = next((item for item in self._catalog.models if item.ref == model_ref), None)
        if model is None or model.pricing != "subscription":
            raise CaptureError(f"{model_ref} is not a subscription model in the catalog")
        if result.failure is FailureKind.SPAWN:
            return CaptureOutcome(False, result.detail, None)
        summary = result.summary
        usage = summary.usage if summary else None
        tokens = Tokens.model_validate(
            {
                "in": usage.input_tokens if usage else 0,
                "out": usage.output_tokens if usage else 0,
                "reasoning": usage.reasoning_tokens if usage else 0,
                "cache_read": usage.cache_read_tokens if usage else 0,
                "cache_write": usage.cache_write_tokens if usage else 0,
            }
        )
        status, error_class = (
            _FAILURE.get(result.failure, (None, None)) if result.failure else (200, None)
        )
        verified = result.ok and result.model_served_verified
        if result.ok and not verified:
            status, error_class = 200, "model_refused"
        served = summary.model_served if summary and verified else None
        seq = self._next_seq()
        call = build_cli_call(
            call_id=f"{trial_id}-cli-{seq}",
            seq=seq,
            run_id=run_id,
            trial_id=trial_id,
            provider=model_ref.split("/", 1)[0],
            account_id=account_id,
            model_ref=model_ref,
            runtime=runtime,
            model_served=served,
            tokens=tokens,
            status=status,
            error_class=error_class,
            total_ms=round(result.duration_seconds * 1000),
        )
        recorded = self._ledger.record(call)
        await self._events.append(call_finished_event(recorded, runtime))
        # A subscription call is reserved at zero and settled with its tokens, so it can never
        # count toward the metered cap. The report keeps these tokens apart.
        await self._budget.record_call(recorded, self._events, subscription=True)
        if not result.ok:
            return CaptureOutcome(False, result.detail, recorded)
        if usage is None:
            return CaptureOutcome(False, "the CLI reported no usage", recorded)
        if not verified:
            return CaptureOutcome(False, "the served model could not be verified", recorded)
        return CaptureOutcome(True, None, recorded)

"""Calls and events for usage a vendor CLI reported about its own run.

The gateway meters a call itself. A `local-cli` trial has no gateway in the path, so all the arena
knows is what the CLI said afterwards. Those records use the same `Call` shape and are labeled
less direct, with no schema change: `upstream` is `cli_reported`, `protocol_in` is `local-cli`
and `protocol_out` names the CLI. The `call_finished` event carries the same label in `data`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from arena.core.models import Call, ErrorClass, RunEvent, Tokens, Try

CLI_SOURCE = "cli_reported"
CLI_PROTOCOL_IN = "local-cli"


def build_cli_call(
    *,
    call_id: str,
    seq: int,
    run_id: str,
    trial_id: str,
    provider: str,
    account_id: str,
    model_ref: str,
    runtime: str,
    model_served: str | None,
    tokens: Tokens,
    status: int | None,
    error_class: ErrorClass | None,
    total_ms: int,
) -> Call:
    """One call record for a CLI run. The ledger prices it (a subscription model has no price)."""
    return Call(
        id=call_id,
        seq=seq,
        run_id=run_id,
        trial_id=trial_id,
        protocol_in=CLI_PROTOCOL_IN,
        protocol_out=f"cli:{runtime}",
        provider=provider,
        account_id=account_id,
        model_asked=model_ref,
        model_served=model_served,
        upstream=CLI_SOURCE,
        tries=[Try(account_id=account_id, status=status, error_class=error_class, ms=total_ms)],
        status=status,
        error_class=error_class,
        tokens=tokens,
        total_ms=total_ms,
    )


def call_finished_event(call: Call, runtime: str) -> RunEvent:
    """The event the Live and Ops views read for a finished call, labeled `cli_reported`."""
    return RunEvent(
        seq=0,
        ts=datetime.now(UTC),
        run_id=call.run_id,
        kind="call_finished",
        ref=call.id,
        data={
            "trial_id": call.trial_id,
            "status": call.status,
            "provider": call.provider,
            "model_asked": call.model_asked,
            "model_served": call.model_served,
            "translated": False,
            "tokens": call.tokens.model_dump(mode="json"),
            "cost_usd": call.cost_usd,
            "queue_ms": 0,
            "total_ms": call.total_ms,
            "source": CLI_SOURCE,
            "runtime": runtime,
        },
    )

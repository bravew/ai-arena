"""Run-level identity and call polling for a gateway container."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from arena.agents.base import AgentProtocol
from arena.core.models import Call
from arena.core.store import Store
from arena.gateway.auth import Purpose, parse_token, token_for_trial
from arena.gateway.server import CallContext


class RunGateway:
    """Allocate call identities and read persisted calls for each trial."""

    def __init__(self, run_id: str, store: Store, *, account_id: str = "provider") -> None:
        token_for_trial("trial-validation")
        if not run_id or not account_id:
            raise ValueError("run and account identities must not be empty")
        self.run_id = run_id
        self.store = store
        self.account_id = account_id
        self._sequence = 0
        self._lock = threading.Lock()

    def token(self, trial_id: str) -> str:
        return token_for_trial(trial_id)

    def context(self, trial_id: str) -> CallContext:
        token_for_trial(trial_id)
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        return CallContext(
            run_id=self.run_id,
            account_id=self.account_id,
            call_id=f"{self.run_id}-{sequence}",
            seq=sequence,
            cost_usd=None,
        )

    def calls_for_trial(self, trial_id: str) -> list[Call]:
        token_for_trial(trial_id)
        rows = self.store.execute(
            "SELECT details_json FROM calls WHERE run_id=? AND trial_id=? ORDER BY seq",
            (self.run_id, trial_id),
        ).fetchall()
        return [Call.model_validate(json.loads(row[0])) for row in rows]

    async def wait_for_call(
        self, trial_token: str, protocol: AgentProtocol, timeout: float
    ) -> bool:
        trial_id = _trial_from_token(trial_token)
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if any(call.protocol_in == protocol for call in self.calls_for_trial(trial_id)):
                return True
            await asyncio.sleep(min(0.05, max(0.0, deadline - asyncio.get_running_loop().time())))
        return any(call.protocol_in == protocol for call in self.calls_for_trial(trial_id))

    async def calls(self, trial_token: str) -> list[Call]:
        return self.calls_for_trial(_trial_from_token(trial_token))


def _trial_from_token(value: str) -> str:
    caller = parse_token(value)
    if caller is None or caller.purpose is not Purpose.TRIAL or caller.trial_id is None:
        raise ValueError("gateway observer requires a trial token")
    return caller.trial_id


def read_event_file(path: Path, run_id: str) -> list[dict[str, Any]]:
    """Read appended events written by the separate gateway process."""
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("run_id") != run_id:
            raise ValueError("gateway event file contains a different run")
        events.append(row)
    return events

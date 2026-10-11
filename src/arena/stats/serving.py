"""How a contestant's model was served, read from its trials and calls.

A subscription result carries no dollar price, so a leaderboard that shows it beside a metered
API result must say which is which. The mode comes from what was recorded, never from config:
a `cli_reported` call came from a vendor CLI run, a trial flagged `subscription_served` went
through the gateway on a subscription, and any other call was metered on an API key.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from arena.core.models import Call, Trial

CLI_REPORTED = "cli_reported"

ServingMode = Literal["api", "subscription-gateway", "subscription-cli", "mixed", "not-recorded"]
SERVING_LABELS: dict[ServingMode, str] = {
    "api": "api",
    "subscription-gateway": "subscription (gateway)",
    "subscription-cli": "subscription (local CLI)",
    "mixed": "mixed serving",
    "not-recorded": "not recorded",
}
_SUBSCRIPTION: frozenset[ServingMode] = frozenset({"subscription-gateway", "subscription-cli"})


def trial_serving(trial: Trial, calls: Iterable[Call]) -> ServingMode:
    """The mode of one trial. A CLI-reported call wins over the gateway flag."""
    recorded = list(calls)
    if any(call.upstream == CLI_REPORTED for call in recorded):
        return "subscription-cli"
    if trial.flags.subscription_served:
        return "subscription-gateway"
    return "api" if recorded else "not-recorded"


def contestant_serving(modes: Iterable[ServingMode]) -> ServingMode:
    """One mode for a contestant. Trials served differently make it `mixed`, never a guess."""
    seen: set[ServingMode] = {mode for mode in modes if mode != "not-recorded"}
    if not seen:
        return "not-recorded"
    return next(iter(seen)) if len(seen) == 1 else "mixed"


def is_subscription_backed(mode: ServingMode) -> bool:
    """Whether any of the contestant's result is a flat-fee one rather than a metered price."""
    return mode in _SUBSCRIPTION or mode == "mixed"

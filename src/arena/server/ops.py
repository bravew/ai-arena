"""Live data for the Ops page: `GET /api/ops`, mounted on the viewer server's route registry.

Everything comes from the run store (the calls ledger and runs), the vendor-CLI probes and the
redacted account list. Nothing here reads a token, a sign-in file or an environment secret, so
nothing of the kind can reach the browser. The route sits behind the server's own sign-in check,
which in remote mode needs the run key.

Not every panel has a live source in the arena process. There is no gateway snapshot to read
lanes from, and no hook instrumentation, so those are empty and `sources` says why. Meters are
arena-counted tokens, not the vendor's own quota.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from arena.core.store import Store
from arena.providers.accounts import AccountStore
from arena.providers.runtimes import RuntimeReport, detect_all
from arena.server.app import ArenaHTTPServer

CALL_LIMIT = 200
USAGE_CALL_LIMIT = 5000
HEALTH_TTL_SECONDS = 30.0

Detect = Callable[[], list[RuntimeReport]]


class ProviderHealth:
    """Vendor-CLI probes cached for a short time, because each one starts a process."""

    def __init__(self, detect: Detect, ttl: float = HEALTH_TTL_SECONDS) -> None:
        self._detect = detect
        self._ttl = ttl
        self._lock = threading.Lock()
        self._at = 0.0
        self._reports: list[dict[str, Any]] = []

    def reports(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._reports or time.monotonic() - self._at > self._ttl:
                self._reports = [report.to_dict() for report in self._detect()]
                self._at = time.monotonic()
            return self._reports


def _calls(store: Store) -> list[dict[str, Any]]:
    rows = store.execute(
        "SELECT details_json FROM calls ORDER BY rowid DESC LIMIT ?", (CALL_LIMIT,)
    ).fetchall()
    return [json.loads(row["details_json"]) for row in rows]


def _usage(store: Store) -> list[dict[str, Any]]:
    """Tokens and mean latency per run, oldest first. The ledger keeps no per-call clock."""
    rows = store.execute(
        "SELECT r.created_at AS label, c.tokens_json AS tokens, c.details_json AS details "
        "FROM calls c JOIN runs r ON r.id = c.run_id "
        "ORDER BY c.rowid DESC LIMIT ?",
        (USAGE_CALL_LIMIT,),
    ).fetchall()
    tokens: dict[str, int] = defaultdict(int)
    latency: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        label = str(row["label"])
        tokens[label] += sum(int(value) for value in json.loads(row["tokens"]).values())
        latency[label].append(int(json.loads(row["details"]).get("total_ms") or 0))
    return [
        {
            "label": label,
            "tokens": tokens[label],
            "latencyMs": round(sum(latency[label]) / len(latency[label])),
        }
        for label in sorted(tokens)
    ]


def _meters(store: Store) -> list[dict[str, Any]]:
    """Arena-counted tokens for calls with no price, which is how subscription calls record."""
    rows = store.execute(
        "SELECT provider, account_id, model_asked, tokens_json FROM calls "
        "WHERE cost_usd IS NULL AND status BETWEEN 200 AND 299"
    ).fetchall()
    used: dict[tuple[str, str, str], int] = defaultdict(int)
    for row in rows:
        key = (str(row["provider"]), str(row["account_id"]), str(row["model_asked"]))
        used[key] += sum(int(value) for value in json.loads(row["tokens_json"]).values())
    return [
        {
            "account": account,
            "provider": provider,
            "model": model,
            "label": "arena-counted tokens",
            "used": count,
            "limit": None,
            "resetsAt": None,
            "source": "arena-counted",
        }
        for (provider, account, model), count in sorted(used.items())
    ]


def _accounts(accounts: AccountStore) -> list[dict[str, str]]:
    """Stored sign-ins by vendor and redacted id. The id is a hash and never a token."""
    return [
        {
            "vendor": account.vendor,
            "id": account.account_id,
            "source": account.source,
            "savedAt": account.saved_at,
        }
        for account in accounts.list()
    ]


def snapshot(home: Path, health: ProviderHealth) -> dict[str, Any]:
    db = home / "arena.db"
    calls: list[dict[str, Any]] = []
    usage: list[dict[str, Any]] = []
    meters: list[dict[str, Any]] = []
    if db.is_file() and db.stat().st_size > 0:
        with Store(db) as store:
            calls, usage, meters = _calls(store), _usage(store), _meters(store)
    return {
        "calls": calls,
        "lanes": {},
        "usage": usage,
        "meters": meters,
        "hooks": [],
        "providers": health.reports(),
        "accounts": _accounts(AccountStore(home / "accounts")),
        "sources": {
            "calls": "ledger",
            "usage": "ledger, one point per run",
            "meters": "arena-counted tokens; the vendor's own quota is not read",
            "lanes": "unavailable: the viewer has no gateway snapshot",
            "hooks": "unavailable: no hook instrumentation is wired to the viewer",
            "providers": "vendor CLI status probes, cached for 30 seconds",
        },
    }


def register_ops_routes(
    server: ArenaHTTPServer,
    home: Path,
    *,
    detect: Detect | None = None,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Add `GET /api/ops`."""
    health = ProviderHealth(detect or (lambda: detect_all(environ)))

    def get_ops(handler: Any, _match: str) -> None:
        try:
            handler.send_json(200, snapshot(home, health))
        except (OSError, ValueError, sqlite3.Error) as error:
            handler.send_json(500, {"error": f"cannot read ops data: {error}"})

    server.register_route("GET", r"/api/ops", get_ops)

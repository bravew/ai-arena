"""Seed an arena home for the live Ops browser test without real accounts or model calls.

Usage: python -I tests/server/seed_ops_home.py <home>
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from arena.catalog.config import ModelCatalog
from arena.core.models import Call, Tokens
from arena.core.store import Store
from arena.obs.ledger import CallsLedger


def seed(home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    catalog = ModelCatalog.model_validate(
        {
            "price_version": "test",
            "models": [
                {"ref": "anthropic-max/claude-opus-5-5", "pricing": "subscription"},
                {"ref": "openai/gpt-6", "price_per_mtok": {"in": 1, "out": 1}},
            ],
        }
    )
    with Store(home / "arena.db") as store:
        store.execute(
            "INSERT INTO runs (id, config_json, status, created_at) "
            "VALUES (?, '{}', 'succeeded', ?)",
            ("ops-seeded", datetime.now(UTC).isoformat()),
        )
        CallsLedger(store, catalog).record(
            Call(
                id="ops-subscription-call",
                seq=1,
                run_id="ops-seeded",
                trial_id=None,
                protocol_in="local-cli",
                protocol_out="cli:claude",
                provider="anthropic-max",
                account_id="0123abcd",
                model_asked="anthropic-max/claude-opus-5-5",
                model_served="claude-opus-5-5",
                upstream="cli_reported",
                status=200,
                tokens=Tokens.model_validate({"in": 120, "out": 32}),
                cost_usd=None,
                queue_ms=0,
                total_ms=812,
                purpose="contestant",
                translated=False,
                swapped=False,
                tries=[],
                cache="off",
                prompt_parts=[],
            )
        )
        CallsLedger(store, catalog).record(
            Call(
                id="ops-api-call",
                seq=2,
                run_id="ops-seeded",
                trial_id=None,
                protocol_in="responses",
                protocol_out="responses",
                provider="openai",
                account_id="api-hash",
                model_asked="openai/gpt-6",
                model_served="gpt-6",
                status=200,
                tokens=Tokens.model_validate({"in": 50, "out": 20}),
                queue_ms=2,
                total_ms=430,
                purpose="contestant",
                translated=False,
                swapped=False,
                tries=[],
                cache="off",
                prompt_parts=[],
            )
        )
    cli_dir = home / "fake-bin"
    cli_dir.mkdir(exist_ok=True)
    claude = cli_dir / "claude"
    claude.write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        "'--version ') printf '2.1.296 (Claude Code)\\n' ;;\n"
        '\'auth status\') printf \'{"loggedIn":false,"authMethod":"none"}\\n\' ;;\n'
        "*) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    claude.chmod(0o755)
    return claude


if __name__ == "__main__":
    print(seed(Path(sys.argv[1])))

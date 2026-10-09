"""Persistent, catalog-priced call records."""

from __future__ import annotations

import json
from typing import Any

from arena.catalog.config import ModelCatalog
from arena.core.models import Call
from arena.core.store import Store


class CallsLedger:
    """Write complete call records to CP1's calls table and read them back."""

    def __init__(self, store: Store, catalog: ModelCatalog) -> None:
        self._store = store
        self._catalog = catalog
        self._models = {model.ref: model for model in catalog.models}

    def record(self, call: Call) -> Call:
        model_ref = call.model_asked.partition(":")[0]
        if "/" not in model_ref:
            model_ref = f"{call.provider}/{model_ref}"
        model = self._models.get(model_ref)
        if model is None:
            raise ValueError(f"call model is not in the catalog: {model_ref}")
        if model.pricing == "subscription":
            priced = call.model_copy(update={"cost_usd": None, "price_version": None})
        else:
            prices = model.price_per_mtok
            if prices is None:
                raise ValueError(f"catalog model has no price: {model_ref}")
            token_counts = {
                "in": call.tokens.in_,
                "out": call.tokens.out,
                "cache_read": call.tokens.cache_read,
                "cache_write": call.tokens.cache_write,
            }
            if call.tokens.reasoning and "reasoning" in prices:
                token_counts["reasoning"] = call.tokens.reasoning
            cost = (
                sum(token_counts.get(kind, 0) * price for kind, price in prices.items())
                / 1_000_000
            )
            priced = call.model_copy(
                update={"cost_usd": cost, "price_version": self._catalog.price_version}
            )

        self._store.execute(
            "INSERT INTO calls(id, seq, run_id, trial_id, provider, account_id, model_asked, "
            "model_served, status, error_class, tokens_json, cost_usd, details_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                priced.id,
                priced.seq,
                priced.run_id,
                priced.trial_id,
                priced.provider,
                priced.account_id,
                priced.model_asked,
                priced.model_served,
                priced.status,
                priced.error_class,
                json.dumps(priced.tokens.model_dump(mode="json"), separators=(",", ":")),
                priced.cost_usd,
                json.dumps(priced.model_dump(mode="json"), separators=(",", ":")),
            ),
        )
        return priced

    def get(self, call_id: str) -> Call | None:
        row = self._store.execute(
            "SELECT details_json FROM calls WHERE id = ?", (call_id,)
        ).fetchone()
        if row is None:
            return None
        data: Any = json.loads(row[0])
        return Call.model_validate(data)

    def list_for_run(self, run_id: str) -> list[Call]:
        rows = self._store.execute(
            "SELECT details_json FROM calls WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
        return [Call.model_validate(json.loads(row[0])) for row in rows]

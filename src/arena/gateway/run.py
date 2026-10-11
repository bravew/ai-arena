"""Run-scoped production gateway composition for the agent runner."""

from __future__ import annotations

import argparse
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import uvicorn
from starlette.applications import Starlette

from arena.catalog.config import ModelCatalog, load_catalog
from arena.core.store import Store
from arena.gateway.auth import Purpose, token_for_trial
from arena.gateway.budget import Budget
from arena.gateway.endpoints import EndpointResolver
from arena.gateway.litellm_adapter import create_production_app
from arena.gateway.server import CallContext, CallContextProvider
from arena.obs.events import EventLog
from arena.obs.ledger import CallsLedger
from arena.providers.config import load_providers
from arena.providers.subscriptions import SubscriptionStore


class RunContextProvider:
    """Allocate unique, run-attributed call identities for trial tokens."""

    def __init__(
        self, run_id: str, trial_ids: Sequence[str], account_id: str = "provider-account"
    ) -> None:
        if not run_id or not account_id or not trial_ids:
            raise ValueError("run and account IDs and at least one trial are required")
        if len(set(trial_ids)) != len(trial_ids):
            raise ValueError("trial IDs must be unique")
        if run_id in {"ops"} or run_id.startswith("judge-"):
            raise ValueError("run ID collides with a reserved gateway identity")
        token_for_trial(run_id)
        self.run_id = run_id
        self.account_id = account_id
        for trial_id in trial_ids:
            token_for_trial(trial_id)
        self.trial_ids = frozenset(trial_ids)
        self._catalog: ModelCatalog | None = None
        self._contexts: dict[str, CallContext] = {}
        self._sequence = 0
        self._lock = threading.Lock()

    def set_catalog(self, catalog: ModelCatalog) -> None:
        self._catalog = catalog

    def __call__(self, target: object, caller: object) -> CallContext | None:
        if getattr(caller, "purpose", None) is not Purpose.TRIAL:
            return None
        trial_id = getattr(caller, "trial_id", None)
        if trial_id not in self.trial_ids:
            return None
        model_ref = getattr(target, "catalog_ref", None)
        if not isinstance(model_ref, str):
            return None
        if self._catalog is None:
            return None
        catalog_model = next((item for item in self._catalog.models if item.ref == model_ref), None)
        if catalog_model is None:
            return None
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            context = CallContext(
                run_id=self.run_id,
                account_id=self.account_id,
                call_id=f"{self.run_id}-{sequence}",
                seq=sequence,
                cost_usd=None,
                price_version=self._catalog.price_version,
            )
            self._contexts[f"{trial_id}:{sequence}"] = context
        return context

    def context_for(self, trial_id: str, sequence: int) -> CallContext | None:
        with self._lock:
            return self._contexts.get(f"{trial_id}:{sequence}")


def create_run_app(
    *,
    run_id: str,
    trial_ids: Sequence[str],
    providers_path: Path,
    catalog_path: Path,
    database_path: Path,
    event_root: Path,
    subscriptions_root: Path,
) -> Starlette:
    providers = load_providers(providers_path)
    catalog = load_catalog(catalog_path)
    store = Store(database_path)
    events = EventLog(event_root)
    ledger = CallsLedger(store, catalog)
    budget = Budget()
    context_provider = RunContextProvider(run_id, trial_ids)
    context_provider.set_catalog(catalog)
    app = create_production_app(
        providers,
        catalog,
        endpoint_resolver=EndpointResolver(subscriptions=SubscriptionStore(subscriptions_root)),
        context_provider=cast(CallContextProvider, context_provider),
        budget=budget,
        ledger=ledger,
        events=events,
    )
    app.state.run_store = store
    app.state.run_events = events
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--trial-id", action="append", required=True)
    parser.add_argument("--providers", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--subscriptions", type=Path, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    options = parser.parse_args()
    app = create_run_app(
        run_id=options.run_id,
        trial_ids=options.trial_id,
        providers_path=options.providers,
        catalog_path=options.catalog,
        database_path=options.database,
        event_root=options.events,
        subscriptions_root=options.subscriptions,
    )
    uvicorn.run(app, host=options.host, port=options.port)


if __name__ == "__main__":
    main()

"""Run-scoped production gateway composition for the agent runner."""

from __future__ import annotations

import argparse
import asyncio
import threading
from collections.abc import Mapping
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
        self,
        run_id: str,
        trial_models: Mapping[str, str],
        account_id: str = "provider-account",
    ) -> None:
        if not run_id or not account_id or not trial_models:
            raise ValueError("run and account IDs and at least one trial are required")
        if run_id in {"ops"} or run_id.startswith("judge-"):
            raise ValueError("run ID collides with a reserved gateway identity")
        token_for_trial(run_id)
        self.run_id = run_id
        self.account_id = account_id
        for trial_id in trial_models:
            token_for_trial(trial_id)
        self.trial_models = dict(trial_models)
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
        if not isinstance(trial_id, str):
            return None
        caller_model = self.trial_models.get(trial_id)
        if caller_model is None:
            return None
        model_ref = getattr(target, "catalog_ref", None)
        if not isinstance(model_ref, str) or caller_model != model_ref:
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
                cost_usd=0.0,
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
    trial_models: Mapping[str, str],
    providers_path: Path,
    catalog_path: Path,
    database_path: Path,
    event_root: Path,
    subscriptions_root: Path,
    budget_usd: float = 20.0,
) -> Starlette:
    providers = load_providers(providers_path)
    catalog = load_catalog(catalog_path)
    store = Store(database_path)
    events = EventLog(event_root)
    ledger = CallsLedger(store, catalog)
    budget = Budget(cap_usd=budget_usd)
    context_provider = RunContextProvider(run_id, trial_models)
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

    async def close_resources() -> None:
        store.close()

    app.state.close_run_resources = close_resources
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--trial-model", action="append", required=True)
    parser.add_argument("--providers", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--subscriptions", type=Path, required=True)
    parser.add_argument("--budget-usd", type=float, default=20.0)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    options = parser.parse_args()
    trial_models: dict[str, str] = {}
    for value in options.trial_model:
        trial_id, separator, model_ref = value.partition("=")
        if not separator or not trial_id or not model_ref or trial_id in trial_models:
            parser.error("each --trial-model must be a unique trial-id=model-ref pair")
        token_for_trial(trial_id)
        trial_models[trial_id] = model_ref
    app = create_run_app(
        run_id=options.run_id,
        trial_models=trial_models,
        providers_path=options.providers,
        catalog_path=options.catalog,
        database_path=options.database,
        event_root=options.events,
        subscriptions_root=options.subscriptions,
        budget_usd=options.budget_usd,
    )
    try:
        uvicorn.run(app, host=options.host, port=options.port)
    finally:
        asyncio.run(app.state.close_run_resources())


if __name__ == "__main__":
    main()

"""Provider onboarding status and explicit connection checks for the viewer API."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin

import httpx

from arena.agents.local.signin import SignInError
from arena.catalog.config import load_catalog
from arena.core.ids import content_id
from arena.core.modelref import ModelRef
from arena.core.store import Store
from arena.gateway.budget import Budget
from arena.obs.events import EventLog
from arena.obs.ledger import CallsLedger
from arena.providers.accounts import AccountStore, account_label, resolve_sign_in
from arena.providers.accounts.discovery import discovery_enabled, read_existing
from arena.providers.config import Provider, load_providers, validate_credential_endpoint
from arena.providers.runtimes import (
    AuthState,
    RuntimeReport,
    RuntimeStatus,
    detect_runtime,
    spec_for_vendor,
)
from arena.runners.local_cli import LocalCliRequest, adapter_for_runtime, run_local_cli
from arena.runners.usage_capture import UsageCapture
from arena.server.app import ArenaHTTPServer

Detect = Callable[[Provider], RuntimeReport]
TestRunner = Callable[[LocalCliRequest], Any]


def _provider_status(
    home: Path,
    providers_path: Path,
    environ: Mapping[str, str],
    detect: Detect,
) -> list[dict[str, Any]]:
    providers = load_providers(providers_path)
    result: list[dict[str, Any]] = []
    accounts = AccountStore(home / "accounts")
    for provider in providers.providers:
        subscription = provider.subscription
        extra = provider.model_extra or {}
        kind = (
            "subscription"
            if subscription is not None
            else provider.kind or ("api" if extra.get("apis") else "unknown")
        )
        if subscription is None:
            result.append(
                {
                    "id": provider.id,
                    "kind": kind,
                    "enabled": provider.enabled,
                    "runtime": "gateway",
                    "vendor": None,
                    "cli_found": None,
                    "version": None,
                    "auth": None,
                    "status": "configured" if provider.enabled else "disabled",
                    "hint": None,
                    "key_envs": [key.env for key in provider.keys],
                }
            )
            continue
        report = detect(provider)
        stored = accounts.get(subscription.vendor)
        if stored is not None:
            sign_in = "stored"
        elif discovery_enabled(environ):
            try:
                read_existing(subscription.vendor, environ)
                sign_in = "cli_discovered"
            except SignInError:
                sign_in = "missing"
        else:
            sign_in = "missing"
        result.append(
            {
                "id": provider.id,
                "kind": kind,
                "enabled": provider.enabled,
                "runtime": subscription.runtime,
                "vendor": subscription.vendor,
                "plan": subscription.plan,
                "cli_found": report.binary is not None,
                "binary": report.binary,
                "version": report.version,
                "auth": report.auth.value,
                "sign_in": sign_in,
                "status": report.status.value,
                "detail": report.detail,
                "hint": report.hint,
                "key_envs": [],
            }
        )
    return result


def register_provider_routes(
    server: ArenaHTTPServer,
    home: Path,
    *,
    providers_path: Path = Path("providers.yaml"),
    catalog_path: Path = Path("catalog/models.yaml"),
    environ: Mapping[str, str] | None = None,
    detect: Detect | None = None,
    runner: TestRunner = run_local_cli,
) -> None:
    """Mount `GET /api/providers`, `POST /api/providers/{id}/test` and add instructions."""
    source_env = dict(os.environ if environ is None else environ)

    def default_detect(provider: Provider) -> RuntimeReport:
        return _detect_provider(provider, source_env)

    check: Detect = detect if detect is not None else default_detect
    lock = threading.Lock()
    last_test: dict[str, float] = {}
    api_test_busy: set[str] = set()

    def get_providers(handler: Any, _match: str) -> None:
        try:
            handler.send_json(
                200,
                {
                    "providers": _provider_status(home, providers_path, source_env, check),
                    "instructions": {
                        "subscription": "arena providers add claude|codex",
                        "api": "arena providers add <provider> --env ENV_NAME",
                        "note": (
                            "Name the environment variable only; never paste a key in the viewer."
                        ),
                    },
                },
            )
        except (OSError, ValueError) as error:
            handler.send_json(500, {"error": f"cannot read provider status: {error}"})

    def test_provider(handler: Any, provider_id: str) -> None:
        try:
            config = load_providers(providers_path)
            provider = next((item for item in config.providers if item.id == provider_id), None)
            if provider is None:
                handler.send_json(404, {"error": "provider not found"})
                return
            if not provider.enabled:
                handler.send_json(409, {"error": "provider is disabled"})
                return
            if provider.subscription is not None and provider.subscription.runtime != "local-cli":
                handler.send_json(409, {"error": "gateway subscription test is unavailable"})
                return
            now = time.monotonic()
            is_api = provider.subscription is None
            with lock:
                if now - last_test.get(provider.id, 0.0) < 30.0:
                    handler.send_json(
                        429, {"error": "connection test is rate limited; wait 30 seconds"}
                    )
                    return
                if is_api and provider.id in api_test_busy:
                    handler.send_json(429, {"error": "a connection test is already running"})
                    return
                last_test[provider.id] = now
                if is_api:
                    api_test_busy.add(provider.id)
            try:
                if provider.subscription is not None:
                    outcome = _test_local_cli(home, provider, catalog_path, source_env, runner)
                else:
                    outcome = _test_api(provider, source_env)
                handler.send_json(200 if outcome["ok"] else 422, outcome)
            finally:
                if is_api:
                    with lock:
                        api_test_busy.discard(provider.id)
        except (OSError, ValueError) as error:
            handler.send_json(422, {"ok": False, "error": str(error)})

    server.register_route("GET", r"/api/providers", get_providers)
    server.register_route("POST", r"/api/providers/([A-Za-z0-9._-]+)/test", test_provider)


def _detect_provider(provider: Provider, environ: Mapping[str, str]) -> RuntimeReport:
    subscription = provider.subscription
    spec = spec_for_vendor(subscription.vendor) if subscription is not None else None
    if spec is None:
        return RuntimeReport(
            "unknown",
            "unknown",
            RuntimeStatus.PROTOCOL_FAILURE,
            AuthState.UNKNOWN,
            None,
            None,
            "no CLI contract for provider",
            None,
        )
    return detect_runtime(spec, environ)


def _test_local_cli(
    home: Path,
    provider: Provider,
    catalog_path: Path,
    environ: Mapping[str, str],
    runner: TestRunner,
) -> dict[str, Any]:
    subscription = provider.subscription
    if subscription is None:
        return {"ok": False, "status": "invalid_provider", "message": "subscription required"}
    spec = spec_for_vendor(subscription.vendor)
    if spec is None:
        return {"ok": False, "status": "unsupported", "message": "no CLI contract"}
    report = detect_runtime(spec, environ)
    if report.status is not RuntimeStatus.OK or report.binary is None:
        return {"ok": False, "status": report.status.value, "message": report.hint or report.detail}
    try:
        discovery_env = dict(environ)
        discovery_env["ARENA_DISCOVER_SIGNINS"] = "0"
        sign_in = resolve_sign_in(
            subscription.vendor, AccountStore(home / "accounts"), discovery_env
        )
    except SignInError as error:
        return {"ok": False, "status": "auth_required", "message": str(error)}
    catalog = load_catalog(catalog_path)
    model = next(
        (
            item
            for item in catalog.models
            if item.ref.startswith(f"{provider.id}/") and item.pricing == "subscription"
        ),
        None,
    )
    if model is None:
        return {
            "ok": False,
            "status": "catalog_missing",
            "message": "add a subscription model to the catalog first",
        }
    request = LocalCliRequest(
        adapter=adapter_for_runtime(spec.runtime),
        binary=report.binary,
        model=ModelRef.parse(model.ref).model,
        prompt="Reply with the single word ping.",
        sign_in=sign_in,
        timeout=60,
        environ=environ,
    )
    result = runner(request)
    if hasattr(result, "__await__"):
        result = asyncio.run(result)
    with Store(home / "arena.db") as store:
        ledger = CallsLedger(store, catalog)
        run_id = content_id({"provider_test": provider.id, "at": time.time_ns()})
        trial_id = content_id({"run_id": run_id, "provider": provider.id})
        store.execute(
            "INSERT INTO runs(id, config_json, status) VALUES (?, '{}', 'running')", (run_id,)
        )
        store.execute(
            "INSERT INTO trials(id, run_id, contestant_id, task_id, attempt, status) "
            "VALUES (?, ?, ?, 'provider-test', 1, 'running')",
            (trial_id, run_id, provider.id),
        )
        try:
            capture = UsageCapture(
                ledger, EventLog(home / "provider-tests"), Budget(20), catalog, lambda: 1
            )
            outcome = asyncio.run(
                capture.capture(
                    run_id=run_id,
                    trial_id=trial_id,
                    model_ref=model.ref,
                    runtime=spec.runtime,
                    account_id=f"cli-{account_label(spec.vendor, dict(sign_in.files))}",
                    result=result,
                )
            )
            terminal = "succeeded" if outcome.reached else "errored"
        except Exception:
            terminal = "errored"
            store.execute("UPDATE trials SET status=? WHERE id=?", (terminal, trial_id))
            store.execute("UPDATE runs SET status=? WHERE id=?", (terminal, run_id))
            raise
        store.execute("UPDATE trials SET status=? WHERE id=?", (terminal, trial_id))
        store.execute("UPDATE runs SET status=? WHERE id=?", (terminal, run_id))
    return {
        "ok": outcome.reached,
        "status": "ok" if outcome.reached else "failed",
        "message": "One capped model call was sent through the vendor CLI and recorded."
        if outcome.reached
        else outcome.reason,
        "call_id": outcome.call.id if outcome.call else None,
        "tokens": outcome.call.tokens.model_dump(mode="json") if outcome.call else None,
        "source": "cli_reported",
    }


def _test_api(provider: Provider, environ: Mapping[str, str]) -> dict[str, Any]:
    extra = provider.model_extra or {}
    raw_apis: object = extra.get("apis", {})
    apis: dict[str, str] = {}
    if isinstance(raw_apis, dict):
        typed_apis = cast(dict[str, object], raw_apis)
        for key, value in typed_apis.items():
            if isinstance(value, str):
                apis[key] = value
    if not apis or not provider.keys:
        return {
            "ok": False,
            "status": "not_configured",
            "message": "no API key or protocol configured",
        }
    protocol, endpoint = next(iter(apis.items()))
    env_name = provider.keys[0].env.removeprefix("${").removesuffix("}")
    secret = environ.get(env_name)
    if not secret:
        return {
            "ok": False,
            "status": "auth_required",
            "message": f"environment variable {env_name} is unset",
        }
    try:
        status = _test_api_request(protocol, endpoint, secret)
    except (OSError, ValueError, httpx.HTTPError) as error:
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        return {"ok": False, "status": "connection_error", "message": message}
    return {
        "ok": 200 <= status < 300,
        "status": "ok" if 200 <= status < 300 else "http_error",
        "message": f"One capped request returned HTTP {status}.",
        "http_status": status,
    }


def _test_api_request(protocol: str, endpoint: str, secret: str) -> int:
    try:
        base = validate_credential_endpoint(endpoint).rstrip("/")
    except ValueError as error:
        raise ValueError(
            "provider endpoint must be an absolute HTTPS URL without userinfo"
        ) from error
    headers = {"Authorization": f"Bearer {secret}"}
    if protocol == "anthropic":
        headers = {"x-api-key": secret, "anthropic-version": "2023-06-01"}
        response = httpx.post(
            urljoin(base + "/", "v1/messages"),
            headers=headers,
            json={
                "model": "claude-3-haiku-20240307",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            },
            timeout=10,
            follow_redirects=False,
        )
    elif protocol == "chat":
        response = httpx.post(
            urljoin(base + "/", "chat/completions"),
            headers=headers,
            json={
                "model": "gpt-4o-mini",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            },
            timeout=10,
            follow_redirects=False,
        )
    elif protocol == "responses":
        response = httpx.post(
            urljoin(base + "/", "responses"),
            headers=headers,
            json={"model": "gpt-4o-mini", "max_output_tokens": 1, "input": "ping"},
            timeout=10,
            follow_redirects=False,
        )
    else:
        raise ValueError(f"provider test does not support protocol {protocol!r}")
    return response.status_code

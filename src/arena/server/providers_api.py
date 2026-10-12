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
from arena.catalog.config import ModelCatalog, load_catalog
from arena.core.ids import content_id
from arena.core.modelref import ModelRef
from arena.core.store import Store
from arena.gateway.budget import Budget
from arena.obs.events import EventLog
from arena.obs.ledger import CallsLedger
from arena.providers.accounts import AccountStore, account_label, resolve_sign_in
from arena.providers.accounts.discovery import discovery_enabled, sign_in_exists
from arena.providers.accounts.probe import detect_account
from arena.providers.config import (
    Provider,
    ProviderConfig,
    ProviderKey,
    load_providers,
    reject_plaintext_secrets,
    validate_credential_endpoint,
)
from arena.providers.config_edit import config_snapshot, parse_bytes, revision_bytes
from arena.providers.credentials import credential_environment, credential_status
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
from arena.server.provider_settings import read_body, register_settings_routes

Detect = Callable[[Provider], RuntimeReport]
TestRunner = Callable[[LocalCliRequest], Any]


def _provider_status(
    config: ProviderConfig,
    home: Path,
    environ: Mapping[str, str],
    detect: Detect,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    accounts = AccountStore(home / "accounts")
    for provider in config.providers:
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
            sign_in = (
                "cli_discovered" if sign_in_exists(subscription.vendor, environ) else "missing"
            )
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
        subscription = provider.subscription
        spec = spec_for_vendor(subscription.vendor) if subscription else None
        if spec is None:
            return _detect_provider(provider, source_env)
        try:
            sign_in = resolve_sign_in(
                spec.vendor, AccountStore(home / "accounts"), source_env, keychain=False
            )
        except SignInError:
            return _detect_provider(provider, source_env)
        return detect_account(spec, sign_in, source_env)

    check: Detect = detect if detect is not None else default_detect
    lock = threading.Lock()
    last_test: dict[str, float] = {}
    api_test_busy: set[str] = set()

    def get_providers(handler: Any, _match: str) -> None:
        try:
            # One locked snapshot: the displayed rows and the revisions a client posts
            # back must describe the same bytes, or a concurrent save is silently reverted.
            with config_snapshot(providers_path, catalog_path) as snapshot:
                config = ProviderConfig.model_validate(parse_bytes(snapshot[providers_path]))
                catalog = ModelCatalog.model_validate(parse_bytes(snapshot[catalog_path]))
                revisions = {path: revision_bytes(data) for path, data in snapshot.items()}
            rows = _provider_status(config, home, source_env, check)
            configs = {p.id: p for p in config.providers}
            for row in rows:
                provider = configs[row["id"]]
                public_config = provider.model_dump(mode="json", by_alias=True, exclude_none=True)
                if reject_plaintext_secrets(public_config, providers_path):
                    raise ValueError("provider file contains unsafe credential fields")
                row["config"] = public_config
                row["credentials"] = [
                    {"id": key.id, "env": key.env, **credential_status(home, source_env, key.env)}
                    for key in provider.keys
                ]
                row["models"] = [
                    model.model_dump(mode="json", exclude_none=True)
                    for model in catalog.models
                    if ModelRef.parse(model.ref).provider == provider.id
                ]
            handler.send_json(
                200,
                {
                    "providers": rows,
                    "revision": revisions[providers_path],
                    "catalog_revision": revisions[catalog_path],
                    "capabilities": {
                        "settings": handler.local_settings_access(),
                        "launch": handler.local_settings_access(),
                    },
                    "settings_home": str(home.resolve()),
                    "instructions": {
                        "subscription": "arena providers add claude|codex",
                        "api": "arena providers add <provider> --env ENV_NAME",
                        "note": (
                            "Configure providers and models here. Saved keys stay in Arena's "
                            "private local store; YAML contains environment references only."
                        ),
                    },
                },
            )
        except (OSError, ValueError):
            handler.send_json(500, {"error": "cannot read provider settings; check configuration"})

    def test_provider(handler: Any, provider_id: str) -> None:
        try:
            body = read_body(handler)
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
                    outcome = _test_local_cli(
                        home, provider, catalog_path, source_env, runner, body.get("model")
                    )
                else:
                    raw_apis: object = (provider.model_extra or {}).get("apis", {})
                    if not isinstance(raw_apis, dict):
                        raise ValueError("provider endpoints must be a protocol mapping")
                    for endpoint in cast(dict[str, object], raw_apis).values():
                        if not isinstance(endpoint, str):
                            raise ValueError("provider endpoints must be URL strings")
                        validate_credential_endpoint(endpoint)
                    selected = _selected_key(provider, body)
                    effective_env = credential_environment(
                        home, source_env, [selected.env] if selected is not None else []
                    )
                    outcome = _test_api(provider, effective_env, body, catalog_path)
                handler.send_json(200 if outcome["ok"] else 422, outcome)
            finally:
                if is_api:
                    with lock:
                        api_test_busy.discard(provider.id)
        except (OSError, ValueError):
            handler.send_json(
                422,
                {
                    "ok": False,
                    "error": "invalid provider test configuration; endpoints require HTTPS "
                    "except loopback HTTP, and fields must be valid",
                },
            )
        except Exception:
            handler.send_json(500, {"ok": False, "error": "connection test failed to run"})

    server.register_route("GET", r"/api/providers", get_providers)
    server.register_route("POST", r"/api/providers/([A-Za-z0-9._-]+)/test", test_provider)
    register_settings_routes(server, home, providers_path, catalog_path, source_env)


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
    selected_model: str | None = None,
) -> dict[str, Any]:
    subscription = provider.subscription
    if subscription is None:
        return {"ok": False, "status": "invalid_provider", "message": "subscription required"}
    spec = spec_for_vendor(subscription.vendor)
    if spec is None:
        return {"ok": False, "status": "unsupported", "message": "no CLI contract"}
    try:
        sign_in = resolve_sign_in(subscription.vendor, AccountStore(home / "accounts"), environ)
    except SignInError:
        return {"ok": False, "status": "auth_required", "message": "Connect a subscription first."}
    report = detect_account(spec, sign_in, environ)
    if report.status is not RuntimeStatus.OK or report.binary is None:
        return {"ok": False, "status": report.status.value, "message": report.hint or report.detail}
    catalog = load_catalog(catalog_path)
    model = next(
        (
            item
            for item in catalog.models
            if item.ref.startswith(f"{provider.id}/")
            and item.pricing == "subscription"
            and (selected_model is None or item.ref == selected_model)
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
        events = EventLog(home / "provider-tests")
        try:
            capture = UsageCapture(ledger, events, Budget(20), catalog, lambda: 1)
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
        finally:
            # The writer lock is per open descriptor; without this every later test is refused.
            events.close()
        store.execute("UPDATE trials SET status=? WHERE id=?", (terminal, trial_id))
        store.execute("UPDATE runs SET status=? WHERE id=?", (terminal, run_id))
    return {
        "ok": outcome.reached,
        "status": "ok" if outcome.reached else "failed",
        "message": "One vendor CLI test was recorded with a 60-second timeout."
        if outcome.reached
        else outcome.reason,
        "call_id": outcome.call.id if outcome.call else None,
        "tokens": outcome.call.tokens.model_dump(mode="json") if outcome.call else None,
        "source": "cli_reported",
    }


def _selected_key(provider: Provider, selection: Mapping[str, Any] | None) -> ProviderKey | None:
    """The one key a test uses: the named key, else the first, else none when a name missed."""
    selection = selection or {}
    return next(
        (key for key in provider.keys if key.id == selection.get("key_id")),
        provider.keys[0] if "key_id" not in selection and provider.keys else None,
    )


def _test_api(
    provider: Provider,
    environ: Mapping[str, str],
    selection: Mapping[str, Any] | None = None,
    catalog_path: Path | None = None,
) -> dict[str, Any]:
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
    selection = selection or {}
    protocol = selection.get("protocol") or next(iter(apis))
    if not isinstance(protocol, str) or protocol not in apis:
        raise ValueError("protocol is not configured")
    endpoint = validate_credential_endpoint(apis[protocol])
    key = _selected_key(provider, selection)
    if key is None:
        raise ValueError("key is not configured")
    model_name: str | None = None
    if catalog_path is not None:
        candidates = [
            model
            for model in load_catalog(catalog_path).models
            if ModelRef.parse(model.ref).provider == provider.id
            and protocol in (model.model_extra or {}).get("protocols", [])
            and model.pricing is None
        ]
        selected = selection.get("model")
        model = next((model for model in candidates if model.ref == selected), None)
        if model is None and selected is None and candidates:
            model = candidates[0]
        if model is None:
            return {"ok": False, "status": "catalog_missing", "message": "Select a catalog model."}
        model_name = ModelRef.parse(model.ref).model
    env_name = key.env.removeprefix("${").removesuffix("}")
    secret = environ.get(env_name)
    if not secret:
        return {
            "ok": False,
            "status": "auth_required",
            "message": f"environment variable {env_name} is unset",
        }
    try:
        status = (
            _test_api_request(protocol, endpoint, secret, model_name)
            if model_name is not None
            else _test_api_request(protocol, endpoint, secret)
        )
    except (OSError, ValueError, httpx.HTTPError) as error:
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        return {"ok": False, "status": "connection_error", "message": message}
    return {
        "ok": 200 <= status < 300,
        "status": "ok" if 200 <= status < 300 else "http_error",
        "message": f"One capped request returned HTTP {status}.",
        "http_status": status,
    }


def _test_api_request(protocol: str, endpoint: str, secret: str, model: str | None = None) -> int:
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
                "model": model or "claude-3-haiku-20240307",
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
                "model": model or "gpt-4o-mini",
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
            json={"model": model or "gpt-4o-mini", "max_output_tokens": 1, "input": "ping"},
            timeout=10,
            follow_redirects=False,
        )
    else:
        raise ValueError(f"provider test does not support protocol {protocol!r}")
    return response.status_code

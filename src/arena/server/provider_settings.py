"""Local-only provider settings and subscription onboarding routes."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from arena.agents.local.signin import SignInError
from arena.providers.accounts import (
    AccountError,
    AccountStore,
    KeychainError,
    LoginError,
    read_claude_keychain,
    read_existing,
)
from arena.providers.accounts.probe import valid_sign_in
from arena.providers.accounts.web_login import LoginManager
from arena.providers.config import load_providers
from arena.providers.config_edit import (
    ConfigEditError,
    RevisionConflict,
    config_lock,
    config_locks,
    save_model,
    save_provider,
)
from arena.providers.credentials import ApiCredentialStore
from arena.providers.models_probe import DiscoveryError, discover_models
from arena.server.app import ArenaHTTPServer

_BODY_LIMIT = 65536


def read_body(handler: Any) -> dict[str, Any]:
    if handler.headers.get("Transfer-Encoding"):
        raise ConfigEditError("request must use a bounded JSON body")
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if length < 1 or length > _BODY_LIMIT:
            raise ValueError
        if handler.headers.get_content_type() != "application/json":
            raise ValueError
        value = json.loads(handler.rfile.read(length))
    except (ValueError, UnicodeDecodeError) as error:
        raise ConfigEditError("request must use a bounded JSON object") from error
    if not isinstance(value, dict):
        raise ConfigEditError("request must use a JSON object")
    return cast(dict[str, Any], value)


def _text(body: dict[str, Any], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value:
        raise ConfigEditError(f"{name} is required")
    return value


def _object(body: dict[str, Any], name: str) -> dict[str, Any]:
    value = body.get(name)
    if not isinstance(value, dict):
        raise ConfigEditError(f"{name} must be an object")
    return cast(dict[str, Any], value)


def register_settings_routes(
    server: ArenaHTTPServer,
    home: Path,
    providers_path: Path,
    catalog_path: Path,
    environ: Mapping[str, str],
) -> None:
    manager = LoginManager(home, environ)
    server.close_callbacks.append(manager.close)
    account_lock = threading.Lock()

    def guarded(action: Callable[[Any, str, dict[str, Any]], object]) -> Callable[..., None]:
        def handle(handler: Any, match: str) -> None:
            if not handler.local_settings_access(mutation=True):
                handler.send_json(403, {"error": "settings require direct local access and CSRF"})
                return
            try:
                result = action(handler, match, read_body(handler))
                handler.send_json(200, result or {"ok": True})
            except RevisionConflict:
                handler.send_json(409, {"error": "settings changed; reload before saving"})
            except (ConfigEditError, DiscoveryError) as error:
                handler.send_json(422, {"error": str(error)})
            except (SignInError, KeychainError, AccountError, LoginError) as error:
                # These messages name the missing sign-in or Keychain item and never a secret.
                handler.send_json(422, {"error": str(error)})
            except (OSError, ValueError, RuntimeError):
                handler.send_json(422, {"error": "settings operation failed; check configuration"})

        return handle

    def get_settings(handler: Any, _match: str) -> None:
        if not handler.local_settings_access():
            handler.send_json(403, {"error": "settings are available only on the local server"})
            return
        handler.send_json(200, {"csrf_token": server.settings_token})

    def provider_for(provider_id: str) -> Any:
        provider = next(
            (p for p in load_providers(providers_path).providers if p.id == provider_id), None
        )
        if provider is None:
            raise ConfigEditError("provider not found")
        return provider

    def write_provider(_handler: Any, provider_id: str, body: dict[str, Any]) -> object:
        value = _object(body, "provider")
        if provider_id and value.get("id") != provider_id:
            raise ConfigEditError("provider id cannot be renamed")
        saved = save_provider(
            providers_path, _text(body, "revision"), value, create=not provider_id
        )
        return {"ok": True, "revision": saved}

    def save_key(_handler: Any, match: str, body: dict[str, Any]) -> object:
        provider_id, key_id = match.split("/")
        with config_lock(providers_path):
            provider = provider_for(provider_id)
            key = next((key for key in provider.keys if key.id == key_id), None)
            if key is None:
                raise ConfigEditError("key reference not found")
            ApiCredentialStore(home / "credentials").save(key.env, _text(body, "value"))
        return {"ok": True}

    def forget_key(_handler: Any, match: str, _body: dict[str, Any]) -> object:
        provider_id, key_id = match.split("/")
        with config_lock(providers_path):
            provider = provider_for(provider_id)
            key = next((key for key in provider.keys if key.id == key_id), None)
            if key is None:
                raise ConfigEditError("key reference not found")
            removed = ApiCredentialStore(home / "credentials").forget(key.env)
        if not removed:
            return {"ok": True, "message": "Arena had no saved key for this reference."}
        return {"ok": True, "message": "Arena key removed; environment values remain available."}

    def write_model(_handler: Any, provider_id: str, body: dict[str, Any]) -> object:
        # Save the catalog under the providers lock too, so the provider a model is
        # validated against cannot change between the check and the write.
        with config_locks(providers_path, catalog_path):
            saved = save_model(
                catalog_path,
                _text(body, "revision"),
                _object(body, "model"),
                provider_for(provider_id),
            )
        return {"ok": True, "revision": saved}

    def discover(_handler: Any, provider_id: str, _body: dict[str, Any]) -> object:
        provider = provider_for(provider_id)
        models = discover_models(provider, environ, home=home)
        return {"models": [{"id": model.id, "source": model.source} for model in models]}

    def import_account(_handler: Any, vendor: str, body: dict[str, Any]) -> object:
        if vendor not in {"claude", "chatgpt"}:
            raise ConfigEditError("unsupported subscription vendor")
        with account_lock:
            manager.cancel_vendor(vendor)
            keychain = body.get("keychain", False)
            if not isinstance(keychain, bool):
                raise ConfigEditError("keychain must be a boolean")
            if keychain and vendor != "claude":
                raise ConfigEditError("Keychain import is for Claude on macOS")
            sign_in = read_claude_keychain(environ) if keychain else read_existing(vendor, environ)
            if not valid_sign_in(vendor, sign_in):
                raise ConfigEditError("existing sign-in is not a supported subscription credential")
            account = AccountStore(home / "accounts").save(vendor, sign_in, "web-import")
        return {"ok": True, "vendor": vendor, "account_id": account.account_id}

    def forget_account(_handler: Any, vendor: str, _body: dict[str, Any]) -> object:
        if vendor not in {"claude", "chatgpt"}:
            raise ConfigEditError("unsupported subscription vendor")
        with account_lock:
            manager.disconnect(vendor)
        return {"ok": True, "message": "Arena copy removed; existing CLI sign-in is unchanged."}

    def login(_handler: Any, vendor: str, _body: dict[str, Any]) -> object:
        with account_lock:
            return manager.start(vendor)

    def login_code(_handler: Any, job_id: str, body: dict[str, Any]) -> object:
        return manager.submit_code(job_id, _text(body, "code"))

    def login_cancel(_handler: Any, job_id: str, _body: dict[str, Any]) -> object:
        return manager.cancel(job_id)

    def login_status(handler: Any, job_id: str) -> None:
        if not handler.local_settings_access():
            handler.send_json(403, {"error": "login status requires local access"})
            return
        try:
            handler.send_json(200, manager.status(job_id))
        except (ValueError, RuntimeError):
            handler.send_json(404, {"error": "login session not found"})

    server.register_route("GET", r"/api/provider-settings", get_settings)
    server.register_route("POST", r"/api/providers", guarded(write_provider))
    server.register_route(
        "POST", r"/api/providers/([A-Za-z0-9._-]+)/config", guarded(write_provider)
    )
    server.register_route(
        "POST",
        r"/api/providers/([A-Za-z0-9._-]+/keys/[A-Za-z0-9._-]+)/save",
        guarded(lambda h, m, b: save_key(h, m.replace("/keys/", "/"), b)),
    )
    server.register_route(
        "POST",
        r"/api/providers/([A-Za-z0-9._-]+/keys/[A-Za-z0-9._-]+)/forget",
        guarded(lambda h, m, b: forget_key(h, m.replace("/keys/", "/"), b)),
    )
    server.register_route("POST", r"/api/providers/([A-Za-z0-9._-]+)/models", guarded(write_model))
    server.register_route(
        "POST", r"/api/providers/([A-Za-z0-9._-]+)/models/discover", guarded(discover)
    )
    server.register_route("POST", r"/api/accounts/(claude|chatgpt)/import", guarded(import_account))
    server.register_route("POST", r"/api/accounts/(claude|chatgpt)/forget", guarded(forget_account))
    server.register_route("POST", r"/api/accounts/(claude|chatgpt)/login", guarded(login))
    server.register_route("GET", r"/api/account-logins/([A-Za-z0-9_-]+)", login_status)
    server.register_route("POST", r"/api/account-logins/([A-Za-z0-9_-]+)/code", guarded(login_code))
    server.register_route(
        "POST", r"/api/account-logins/([A-Za-z0-9_-]+)/cancel", guarded(login_cancel)
    )

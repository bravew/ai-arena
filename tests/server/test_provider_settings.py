from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from arena.catalog.config import load_catalog
from arena.providers.config import load_providers
from arena.providers.config_edit import revision
from arena.providers.credentials import ApiCredentialStore
from arena.server.app import create_server


@contextmanager
def settings_server(tmp_path: Path) -> Generator[tuple[str, Path, Path]]:
    providers = tmp_path / "providers.yaml"
    providers.write_text("providers:\n  - id: mock\n    kind: mock\n    custom: keep\n")
    catalog = tmp_path / "models.yaml"
    catalog.write_text(
        "price_version: test\nmodels:\n  - ref: mock/echo\n    price_per_mtok: {in: 0, out: 0}\n"
    )
    server = create_server(
        "127.0.0.1",
        0,
        tmp_path,
        provider_config=providers,
        provider_catalog=catalog,
        provider_environ={"ARENA_DISCOVER_SIGNINS": "0", "PATH": "/usr/bin:/bin"},
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", providers, catalog
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(
    base: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    token: str = "",
    origin: str | None = None,
    host: str | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Arena-CSRF"] = token
    if origin is not None:
        headers["Origin"] = origin
    if host is not None:
        headers["Host"] = host
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
    )
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
    with response:
        return response.status, json.loads(response.read())


def api_provider() -> dict[str, Any]:
    return {
        "id": "custom",
        "apis": {"responses": "https://provider.example/v1"},
        "keys": [{"id": "main", "env": "CUSTOM_KEY"}],
        "limits": {"concurrency": 1},
    }


def test_web_settings_save_provider_key_and_model_without_exposing_key(tmp_path: Path) -> None:
    with settings_server(tmp_path) as (base, providers, catalog):
        _, settings = request(base, "/api/provider-settings")
        token = settings["csrf_token"]
        status, saved = request(
            base,
            "/api/providers",
            {
                "revision": revision(providers),
                "provider": api_provider(),
            },
            token=token,
            origin=base,
        )
        assert status == 200, saved
        secret = "secret-never-returned-in-a-provider-response"
        status, body = request(
            base,
            "/api/providers/custom/keys/main/save",
            {"value": secret},
            token=token,
            origin=base,
        )
        assert status == 200, body
        assert ApiCredentialStore(tmp_path / "credentials").load("CUSTOM_KEY") == secret
        status, body = request(base, "/api/providers")
        assert status == 200, body
        custom = next(p for p in body["providers"] if p["id"] == "custom")
        assert custom["credentials"] == [
            {
                "id": "main",
                "env": "CUSTOM_KEY",
                "configured": True,
                "source": "arena",
            }
        ]
        assert secret not in json.dumps(body)
        assert secret not in providers.read_text()
        status, body = request(
            base,
            "/api/providers/custom/models",
            {
                "revision": revision(catalog),
                "model": {
                    "ref": "custom/model/name",
                    "protocols": ["responses"],
                    "price_per_mtok": {"in": 1, "out": 2},
                    "source": "https://provider.example/prices",
                },
            },
            token=token,
            origin=base,
        )
        assert status == 200, body
        assert load_catalog(catalog).models[-1].ref == "custom/model/name"
        assert load_providers(providers).providers[0].model_extra == {"custom": "keep"}
        status, body = request(
            base, "/api/providers/custom/keys/main/forget", {}, token=token, origin=base
        )
        assert status == 200, body
        assert ApiCredentialStore(tmp_path / "credentials").load("CUSTOM_KEY") is None


@pytest.mark.parametrize("origin", [None, "null", "https://foreign.example"])
def test_settings_reject_missing_or_foreign_origins(tmp_path: Path, origin: str | None) -> None:
    with settings_server(tmp_path) as (base, providers, _catalog):
        _, settings = request(base, "/api/provider-settings")
        status, _ = request(
            base,
            "/api/providers",
            {
                "revision": revision(providers),
                "provider": api_provider(),
            },
            token=settings["csrf_token"],
            origin=origin,
        )
        assert status == 403
        assert len(load_providers(providers).providers) == 1


def test_settings_reject_rebinding_host_and_bad_csrf(tmp_path: Path) -> None:
    with settings_server(tmp_path) as (base, providers, _catalog):
        status, body = request(base, "/api/provider-settings", host="foreign.example:7400")
        assert status == 403, body
        status, _ = request(
            base,
            "/api/providers",
            {
                "revision": revision(providers),
                "provider": api_provider(),
            },
            token="wrong",
            origin=base,
        )
        assert status == 403


def test_settings_revision_conflict_and_invalid_secret_leave_files_unchanged(
    tmp_path: Path,
) -> None:
    with settings_server(tmp_path) as (base, providers, _catalog):
        _, settings = request(base, "/api/provider-settings")
        original = providers.read_bytes()
        token = settings["csrf_token"]
        status, _ = request(
            base,
            "/api/providers",
            {
                "revision": "outdated",
                "provider": api_provider(),
            },
            token=token,
            origin=base,
        )
        assert status == 409
        bad = api_provider()
        bad["headers"] = {"Authorization": "secret-value-must-not-appear"}
        status, body = request(
            base,
            "/api/providers",
            {
                "revision": revision(providers),
                "provider": bad,
            },
            token=token,
            origin=base,
        )
        assert status == 422
        assert "secret-value-must-not-appear" not in json.dumps(body)
        assert providers.read_bytes() == original


def test_settings_remote_mode_does_not_expose_management_token(tmp_path: Path) -> None:
    server = create_server("0.0.0.0", 0, tmp_path, run_key="test-key", trusted_proxy="127.0.0.1")
    try:
        assert not server.settings_enabled
    finally:
        server.server_close()


def test_non_ascii_csrf_header_is_rejected_not_a_dropped_connection(tmp_path: Path) -> None:
    """A Latin-1 header byte used to raise TypeError past the guard and close the socket."""
    with settings_server(tmp_path) as (base, providers, _catalog):
        status, body = request(
            base,
            "/api/providers",
            {"revision": revision(providers), "provider": api_provider()},
            token="é",
            origin=base,
        )
        assert status == 403, body
        assert len(load_providers(providers).providers) == 1


def test_importing_a_missing_sign_in_returns_json_error(tmp_path: Path) -> None:
    """`read_existing` raises SignInError; it must answer 422, not drop the connection."""
    with settings_server(tmp_path) as (base, _providers, _catalog):
        _, settings = request(base, "/api/provider-settings")
        status, body = request(
            base,
            "/api/accounts/claude/import",
            {},
            token=settings["csrf_token"],
            origin=base,
        )
        assert status == 422, body
        assert "error" in body


def test_a_string_apis_field_is_rejected_without_dropping_the_connection(tmp_path: Path) -> None:
    """`apis: https://...` (a string) used to raise AttributeError inside the test route."""
    with settings_server(tmp_path) as (base, providers, _catalog):
        providers.write_text(
            "providers:\n  - id: odd\n    apis: https://provider.example/v1\n"
            "    keys:\n      - id: main\n        env: ODD_KEY\n"
        )
        _, settings = request(base, "/api/provider-settings")
        status, body = request(
            base,
            "/api/providers/odd/test",
            {},
            token=settings["csrf_token"],
            origin=base,
        )
        assert status == 422, body
        assert "error" in body


def test_provider_listing_revision_matches_the_configuration_it_shows(tmp_path: Path) -> None:
    """A posted revision must describe the shown rows, or a save silently reverts an edit."""
    with settings_server(tmp_path) as (base, providers, _catalog):
        status, listing = request(base, "/api/providers")
        assert status == 200, listing
        assert listing["revision"] == revision(providers)
        shown = listing["providers"][0]["config"]
        _, settings = request(base, "/api/provider-settings")
        status, saved = request(
            base,
            f"/api/providers/{shown['id']}/config",
            {"revision": listing["revision"], "provider": shown},
            token=settings["csrf_token"],
            origin=base,
        )
        assert status == 200, saved
        assert saved["revision"] == revision(providers)

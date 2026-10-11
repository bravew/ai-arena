from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from arena.catalog.config import load_catalog
from arena.gateway.auth import (
    Caller,
    Purpose,
    authenticate,
    parse_token,
    token_for_judge,
    token_for_trial,
)
from arena.gateway.redact import RedactingFilter, is_secret_name, scrub, scrub_header, scrub_json
from arena.gateway.resolve import ResolveError, ResolveFailure, listed_models, resolve_target
from arena.gateway.server import create_app
from arena.providers.config import load_providers

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = load_providers(ROOT / "providers.yaml")
CATALOG = load_catalog(ROOT / "catalog" / "models.yaml")
TRIAL_TOKEN = "arena-trial-1"


def _app(*, max_body_bytes: int = 4096):
    return create_app(PROVIDERS, CATALOG, max_body_bytes=max_body_bytes)


def test_protocol_surface_auth_origin_and_models_are_served() -> None:
    client = TestClient(_app())
    paths = [
        "/v1/chat/completions",
        "/v1/responses",
        "/v1/messages",
        "/v1/messages/count_tokens",
        "/v1beta/models/anthropic/claude-opus-5-5:streamGenerateContent",
    ]
    for path in paths:
        method = "GET" if path.endswith("streamGenerateContent") else "POST"
        response = client.request(method, path)
        assert response.status_code == 401

    # This passes through the ASGI middleware and the real running server test client, not a
    # direct call to a handler. The valid token deliberately does not change the Origin result.
    response = client.post(
        "/v1/chat/completions",
        headers={"Origin": "https://attacker.example", "Authorization": f"Bearer {TRIAL_TOKEN}"},
        json={"model": "anthropic/claude-opus-5-5"},
    )
    assert response.status_code == 403

    response = client.get("/v1/models", headers={"Authorization": f"Bearer {TRIAL_TOKEN}"})
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "anthropic/claude-opus-5-5"


def test_token_formats_and_credential_locations() -> None:
    assert token_for_trial("trial-1") == "arena-trial-1"
    assert token_for_judge("run-1") == "arena-judge-run-1"
    assert parse_token("arena-ops") == Caller(Purpose.OPS)
    assert parse_token("arena-judge-run-1") == Caller(Purpose.JUDGE, run_id="run-1")
    assert parse_token("arena-trial-1") == Caller(Purpose.TRIAL, trial_id="trial-1")
    assert authenticate({"authorization": "Bearer arena-trial-1"}, {}) == Caller(
        Purpose.TRIAL, trial_id="trial-1"
    )
    assert authenticate({"x-api-key": "arena-trial-1"}, {}) == Caller(
        Purpose.TRIAL, trial_id="trial-1"
    )
    assert authenticate({"x-goog-api-key": "arena-trial-1"}, {}) == Caller(
        Purpose.TRIAL, trial_id="trial-1"
    )
    assert authenticate({}, {"key": "arena-trial-1"}) == Caller(Purpose.TRIAL, trial_id="trial-1")
    assert authenticate({}, {}) is None
    assert parse_token("arena-judge-") is None
    with pytest.raises(ValueError):
        token_for_trial("judge-run-1")
    with pytest.raises(ValueError):
        token_for_trial("ops")


def test_unknown_model_provider_off_and_not_served_are_distinct(tmp_path: Path) -> None:
    with pytest.raises(ResolveError) as unknown:
        resolve_target("anthropic/no-such-model", PROVIDERS, CATALOG)
    assert unknown.value.failure is ResolveFailure.UNKNOWN_MODEL
    assert "unknown model" in str(unknown.value)

    disabled_yaml = ROOT / "providers.yaml"
    raw = disabled_yaml.read_text(encoding="utf-8").replace(
        "id: anthropic\n", "id: anthropic\n    enabled: false\n"
    )
    with pytest.raises(ResolveError) as off:
        resolve_target(
            "anthropic/claude-opus-5-5",
            load_providers(_write_config(tmp_path, raw)),
            CATALOG,
        )
    assert off.value.failure is ResolveFailure.PROVIDER_OFF
    assert "provider off" in str(off.value)

    # Exercise a valid catalog entry with an incompatible protocol endpoint.
    incompatible_yaml = ROOT / "providers.yaml"
    raw = incompatible_yaml.read_text(encoding="utf-8").replace(
        "apis: {anthropic: https://api.anthropic.com}",
        "apis: {responses: https://api.anthropic.com}",
    )
    with pytest.raises(ResolveError) as not_served:
        resolve_target(
            "anthropic/claude-opus-5-5", load_providers(_write_config(tmp_path, raw)), CATALOG
        )
    assert not_served.value.failure is ResolveFailure.NOT_SERVED
    assert "model not served" in str(not_served.value)


def _write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "providers.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("encoding", ["gzip", "zstd"])
def test_compressed_requests_are_decoded_before_routing(encoding: str) -> None:
    import zstandard

    raw = json.dumps({"model": "anthropic/claude-opus-5-5", "input": "hello"}).encode()
    compressed = (
        gzip.compress(raw) if encoding == "gzip" else zstandard.ZstdCompressor().compress(raw)
    )
    response = TestClient(_app()).post(
        "/v1/responses",
        content=compressed,
        headers={"Authorization": f"Bearer {TRIAL_TOKEN}", "Content-Encoding": encoding},
    )
    assert response.status_code == 501
    assert response.json()["error"]["type"] == "not_implemented"


def test_body_limit_applies_after_decompression_and_before_routing() -> None:
    content = gzip.compress(
        json.dumps({"model": "anthropic/claude-opus-5-5", "input": "x" * 2048}).encode()
    )
    response = TestClient(_app(max_body_bytes=512)).post(
        "/v1/responses",
        content=content,
        headers={"Authorization": f"Bearer {TRIAL_TOKEN}", "Content-Encoding": "gzip"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["type"] == "request_too_large"


def test_redaction_scrubs_fields_headers_logs_and_raw_text() -> None:
    secret = "sk-ant-api03-12345678901234567890"
    cleaned = scrub_json(
        json.dumps(
            {
                "authorization": f"Bearer {secret}",
                "api_key": secret,
                "max_tokens": 8,
                "prompt": f"token={secret}",
                "image": "data:image/png;base64,AAAA",
            }
        ).encode()
    ).decode()
    assert secret not in cleaned
    assert '"authorization":"[REDACTED]"' in cleaned
    assert '"api_key":"[REDACTED]"' in cleaned
    assert '"max_tokens":8' in cleaned
    assert "data:image/png;base64,AAAA" in cleaned
    assert secret not in scrub_header("Authorization", f"Bearer {secret}")
    assert is_secret_name("access_token")
    assert not is_secret_name("max_tokens")

    record = logging.LogRecord("test", logging.INFO, __file__, 1, "key=%s", (secret,), None)
    assert RedactingFilter().filter(record)
    assert secret not in record.getMessage()
    assert secret not in scrub(f"authorization: Bearer {secret}")


def test_authenticated_protocol_rejection_never_logs_raw_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "sk-ant-api03-12345678901234567890"
    response = TestClient(_app()).post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {TRIAL_TOKEN}"},
        json={"model": "anthropic/claude-opus-5-5", "api_key": secret},
    )
    assert response.status_code == 501
    assert secret not in caplog.text
    assert secret not in response.text


def test_provider_off_models_are_omitted() -> None:
    assert [item.ref for item in listed_models(PROVIDERS, CATALOG)] == [
        "anthropic/claude-opus-5-5",
        "anthropic/claude-opus-5-5-sub",
    ]

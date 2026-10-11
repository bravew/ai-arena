"""Browser evidence for the artifact origin's script and cookie isolation."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from arena.core.cas import ArtifactStore
from arena.core.store import Store
from arena.server.app import create_server


def test_artifact_cannot_execute_scripts_or_read_api_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Optional uv dependencies can live in its user cache; importing them must not write there.
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    playwright = pytest.importorskip("playwright.sync_api")
    api = create_server("127.0.0.1", 0, tmp_path)
    artifact = create_server("127.0.0.1", 0, tmp_path, artifacts=True)
    api_url = f"http://127.0.0.1:{api.server_address[1]}"
    artifact_url = f"http://127.0.0.1:{artifact.server_address[1]}"
    payload = (
        "<!doctype html><title>Artifact isolation</title><p>Artifact rendered</p>"
        "<script>document.body.dataset.executed='yes';"
        "document.body.dataset.cookie=document.cookie;"
        f"fetch('{api_url}/api/health');</script>"
        f"<img src='{api_url}/api/health'>"
    ).encode()
    digest = ArtifactStore(tmp_path / "artifacts").put(payload)
    with Store(tmp_path / "arena.db") as store:
        store.execute("INSERT INTO runs (id, config_json, status) VALUES ('r', '{}', 'succeeded')")
        store.execute(
            "INSERT INTO trials (id, run_id, contestant_id, task_id, status) "
            "VALUES ('t', 'r', 'c', 'task', 'succeeded')"
        )
        store.execute(
            "INSERT INTO trial_artifacts(trial_id, path, sha256, mime, render_hint) "
            "VALUES ('t', 'index.html', ?, 'text/html', 'html-sandbox')",
            (digest,),
        )
    threading.Thread(target=api.serve_forever, daemon=True).start()
    threading.Thread(target=artifact.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as browser_api:
            browser = browser_api.chromium.launch()
            context = browser.new_context()
            page = context.new_page()
            received: list[str] = []
            page.on("response", lambda response: received.append(response.url))
            response = page.goto(artifact_url + "/artifacts/" + digest)
            assert response is not None and response.status == 200
            assert page.locator("p").inner_text() == "Artifact rendered"
            assert page.locator("body").get_attribute("data-executed") is None
            assert page.locator("body").get_attribute("data-cookie") is None
            assert page.evaluate("""() => {
                try { return {cookie: document.cookie}; }
                catch (error) { return {error: error.name}; }
            }""") == {"error": "SecurityError"}
            assert api_url + "/api/health" not in received
            browser.close()
    finally:
        api.shutdown()
        artifact.shutdown()
        api.server_close()
        artifact.server_close()

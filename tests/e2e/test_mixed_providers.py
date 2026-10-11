"""A fully local mixed API and subscription run through report, export and Ops."""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.request
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from arena.agents.local.types import SignIn
from arena.catalog.config import load_catalog
from arena.cli import app
from arena.core.bundle_contract import validate_bundle
from arena.core.ids import content_id
from arena.core.models import Call, Tokens
from arena.obs.ledger import CallsLedger
from arena.providers.accounts import AccountStore
from arena.runners.completion import CompletionResult
from arena.runners.metered_gateway import GatewayContainer
from arena.server.app import ArenaHTTPServer, create_server
from tests.providers.fakes import FIXTURES, write_script


class LocalServer:
    def __init__(self, home: Path) -> None:
        self.server: ArenaHTTPServer = create_server(
            "127.0.0.1", 0, home, session_secret=b"mixed-e2e-test-secret"
        )
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> LocalServer:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def _fake_claude(path: Path, calls: Path) -> Path:
    fixture = FIXTURES / "claude"
    return write_script(
        path,
        f"""case "$1" in
--version) cat '{fixture / "version.txt"}'; exit 0 ;;
auth) cat '{fixture / "auth-status.signed-in.json"}'; exit 0 ;;
esac
echo claude >> '{calls}'
echo done > answer.md
sed 's/claude-opus-5-5/claude-opus-5-5-sub/g' '{fixture / "stream.synthetic.jsonl"}'
""",
    )


def _fake_codex(path: Path, calls: Path) -> Path:
    fixture = FIXTURES / "codex"
    return write_script(
        path,
        f"""case "$1 $2" in
"--version ") cat '{fixture / "version.txt"}'; exit 0 ;;
"login status") cat '{fixture / "login-status.signed-in.txt"}'; exit 0 ;;
"debug models") cat '{fixture / "debug-models.empty-home.json"}'; exit 0 ;;
esac
echo codex >> '{calls}'
echo done > answer.md
mkdir -p "$CODEX_HOME/sessions/2026"
cat > "$CODEX_HOME/sessions/2026/rollout-1.jsonl" <<'EOF'
{{"type":"turn_context","payload":{{"model":"gpt-6.1-sol"}}}}
EOF
cat '{fixture / "exec.synthetic.jsonl"}'
""",
    )


@pytest.fixture
def mixed_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    fake_bin = root / "fake-bin"
    fake_bin.mkdir()
    providers = root / "providers.yaml"
    providers.write_text(
        """providers:
  - id: openai
    apis: {responses: http://127.0.0.1:0/v1}
    keys: [{id: main, env: OPENAI_API_KEY}]
  - id: anthropic-max
    apis: {anthropic: https://api.anthropic.com}
    keys: []
    subscription: {vendor: claude, plan: max, import: own-sign-in, runtime: local-cli}
    limits: {concurrency: 1}
  - id: openai-plus
    keys: []
    subscription: {vendor: chatgpt, plan: plus, import: own-sign-in, runtime: local-cli}
    limits: {concurrency: 1}
""",
        encoding="utf-8",
    )
    catalog_path = root / "models.yaml"
    catalog_path.write_text(
        """price_version: test
models:
  - ref: openai/gpt-5.6
    protocols: [responses]
    price_per_mtok: {in: 1, out: 2}
  - ref: anthropic-max/claude-opus-5-5-sub
    protocols: [anthropic]
    pricing: subscription
  - ref: openai-plus/gpt-6.1-sol
    protocols: [responses]
    pricing: subscription
""",
        encoding="utf-8",
    )
    suite_task = root / "suite/tasks/note"
    suite_task.mkdir(parents=True)
    (suite_task / "task.yaml").write_text(
        """id: demo.note
version: 1
kind: content
prompt_file: prompt.md
scorers:
  - {id: constraints, weight: 1.0, gate: true, must_include: [done]}
created_at: '2026-10-11'
""",
        encoding="utf-8",
    )
    (suite_task / "prompt.md").write_text("Say done.\n", encoding="utf-8")
    api_contestant = root / "api.yaml"
    api_contestant.write_text(
        "id_label: api\nmodel: openai/gpt-5.6\nscaffold: {id: opencode, version: 1.2.0}\n",
        encoding="utf-8",
    )
    claude_contestant = root / "claude.yaml"
    claude_contestant.write_text(
        "id_label: claude\nmodel: anthropic-max/claude-opus-5-5-sub\n",
        encoding="utf-8",
    )
    codex_contestant = root / "codex.yaml"
    codex_contestant.write_text(
        "id_label: codex\nmodel: openai-plus/gpt-6.1-sol\n", encoding="utf-8"
    )

    cli_calls = root / "cli-calls.txt"
    claude = _fake_claude(fake_bin / "claude", cli_calls)
    codex = _fake_codex(fake_bin / "codex", cli_calls)
    monkeypatch.setenv("CLAUDE_CODE_PATH", str(claude))
    monkeypatch.setenv("CODEX_CLI_PATH", str(codex))
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setenv("ARENA_DISCOVER_SIGNINS", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    monkeypatch.setenv("HOME", str(home))
    AccountStore(home / "accounts").save(
        "claude", SignIn(files={".claude/.credentials.json": b"{}"}), "test"
    )
    AccountStore(home / "accounts").save(
        "chatgpt", SignIn(files={".codex/auth.json": b'{"auth_mode":"chatgpt"}'}), "test"
    )
    # Codex's model probe reads the installed CLI's home before the runner creates its scratch copy.
    cli_home = home / ".codex"
    cli_home.mkdir()
    (cli_home / "auth.json").write_text('{"auth_mode":"chatgpt"}', encoding="utf-8")

    api: dict[str, Any] = {"calls": 0, "base": ""}
    upstream: ThreadingHTTPServer | None = None

    def start(self: GatewayContainer) -> None:
        nonlocal upstream
        self._started = True

        class FakeUpstream(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                api["calls"] += 1
                body = json.dumps(
                    {
                        "id": "fake-api",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "done"}],
                            }
                        ],
                        "model": "gpt-5.6",
                        "usage": {"input_tokens": 12, "output_tokens": 5},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                return

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
        api["base"] = f"http://127.0.0.1:{upstream.server_address[1]}"
        threading.Thread(target=upstream.serve_forever, daemon=True).start()

    def stop(self: GatewayContainer) -> None:
        nonlocal upstream
        self._started = False
        if upstream is not None:
            upstream.shutdown()
            upstream.server_close()
            upstream = None

    monkeypatch.setattr(GatewayContainer, "preflight", lambda self, _images: None)
    monkeypatch.setattr(GatewayContainer, "running", property(lambda self: self._started))
    monkeypatch.setattr(GatewayContainer, "start", start)
    monkeypatch.setattr(GatewayContainer, "stop", stop)

    async def fake_api_trial(job: Any, run_id: str, **kwargs: Any) -> CompletionResult:
        request = urllib.request.Request(
            f"{api['base']}/v1/responses",
            data=json.dumps(
                {"model": str(job.contestant.model), "input": "Say done.", "max_output_tokens": 16}
            ).encode(),
            headers={"content-type": "application/json", "authorization": "Bearer test-api-key"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            reply = json.loads(response.read())
        trial_id = content_id({"run_id": run_id, "job_id": job.id})
        CallsLedger(kwargs["store"], load_catalog(catalog_path)).record(
            Call(
                id=f"{trial_id}-api-call",
                seq=1,
                run_id=run_id,
                trial_id=trial_id,
                protocol_in="responses",
                protocol_out="responses",
                provider="openai",
                account_id="api-test",
                model_asked=str(job.contestant.model),
                model_served=reply["model"],
                status=200,
                tokens=Tokens.model_validate(
                    {
                        "in": reply["usage"]["input_tokens"],
                        "out": reply["usage"]["output_tokens"],
                    }
                ),
                purpose="contestant",
                translated=False,
                swapped=False,
                tries=[],
                cache="off",
                prompt_parts=[],
            )
        )
        digest = kwargs["artifacts"].put(b"done")
        kwargs["store"].execute(
            "INSERT INTO trial_artifacts(trial_id, path, sha256, mime, render_hint) "
            "VALUES (?, 'answer.md', ?, 'text/markdown', 'markdown')",
            (trial_id, digest),
        )
        return CompletionResult(text="done")

    monkeypatch.setattr("arena.cli_run.execute_agent_trial", fake_api_trial)

    db = root / "arena.db"
    run_args = [
        "run",
        str(root / "suite"),
        "-c",
        str(api_contestant),
        "-c",
        str(claude_contestant),
        "-c",
        str(codex_contestant),
        "--repeats",
        "1",
        "--db",
        str(db),
        "--providers",
        str(providers),
        "--catalog",
        str(catalog_path),
        "--home",
        str(home),
        "--budget-usd",
        "5",
    ]
    run = CliRunner().invoke(app, run_args)
    assert run.exit_code == 0, run.output
    with sqlite3.connect(db) as connection:
        run_id = connection.execute("SELECT id FROM runs").fetchone()[0]
    report = CliRunner().invoke(app, ["report", run_id, "--home", str(db.parent)])
    assert report.exit_code == 0, report.output
    out = root / "export"
    exported = CliRunner().invoke(
        app, ["export", run_id, "--out", str(out), "--home", str(db.parent)]
    )
    assert exported.exit_code == 0, exported.output
    with ExitStack() as stack:
        server = stack.enter_context(LocalServer(db.parent))
        response = stack.enter_context(urllib.request.urlopen(f"{server.base}/api/ops", timeout=3))
        ops = json.loads(response.read())
    return {
        "api": api,
        "calls": cli_calls.read_text(encoding="utf-8").splitlines(),
        "report": report.output,
        "bundle": json.loads((out / "bundle.json").read_text(encoding="utf-8")),
        "ops": ops,
    }


def test_mixed_api_and_both_subscription_vendors_flow_through_report_export_and_ops(
    mixed_run: dict[str, Any],
) -> None:
    assert mixed_run["api"]["calls"] == 1
    assert sorted(mixed_run["calls"]) == ["claude", "codex"]
    report = mixed_run["report"]
    assert "| api | api |" in report
    assert "subscription (local CLI)" in report
    bundle = mixed_run["bundle"]
    validate_bundle(bundle)
    assert {contestant["model"].split("/", 1)[0] for contestant in bundle["contestants"]} == {
        "openai",
        "anthropic-max",
        "openai-plus",
    }
    assert len(bundle["trials"]) == 3
    assert sum(call["upstream"] == "cli_reported" for call in bundle["calls"]) == 2
    assert {call["provider"] for call in bundle["calls"]} == {
        "openai",
        "anthropic-max",
        "openai-plus",
    }
    ops = mixed_run["ops"]
    assert {call["provider"] for call in ops["calls"]} == {"openai", "anthropic-max", "openai-plus"}
    assert {call["upstream"] for call in ops["calls"]} >= {"cli_reported"}
    assert any(meter["source"] == "arena-counted" for meter in ops["meters"])

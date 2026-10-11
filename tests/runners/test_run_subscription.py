"""`arena run` with subscription contestants served by a fake vendor CLI.

No real model is called: `claude` is a shell script that replays the synthetic stream fixture.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.agents.local.types import SignIn
from arena.cli import app
from arena.providers.accounts import AccountStore
from tests.providers.fakes import FIXTURES, write_script

CLAUDE = FIXTURES / "claude"
PROVIDERS = """providers:
  - id: mock
    kind: mock
  - id: claude-plan
    keys: []
    subscription: {vendor: claude, plan: max, import: own-sign-in, runtime: local-cli}
    limits: {concurrency: 1}
"""
CATALOG = """price_version: 2026-10-11
models:
  - ref: claude-plan/claude-opus-5-5
    protocols: [anthropic]
    pricing: subscription
"""
TASK = """id: demo.note
version: 1
kind: content
prompt_file: prompt.md
scorers:
  - {id: constraints, weight: 1.0, gate: true, must_include: ["done"]}
created_at: "2026-10-11"
"""
CONTESTANT = "id_label: {label}\nmodel: {model}\nscaffold: {scaffold}\norchestration: single\n"


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.calls = root / "calls.txt"
        self.fail_marker = root / "fail-once"
        suite = root / "suite" / "tasks" / "note"
        suite.mkdir(parents=True)
        (suite / "task.yaml").write_text(TASK, encoding="utf-8")
        (suite / "prompt.md").write_text("Say done.\n", encoding="utf-8")
        (root / "providers.yaml").write_text(PROVIDERS, encoding="utf-8")
        (root / "models.yaml").write_text(CATALOG, encoding="utf-8")
        self.cli = self._contestant("cli", "claude-plan/claude-opus-5-5")
        self.mock = self._contestant("mock-a", "mock/echo")
        self.binary = self._fake_claude()

    def _contestant(self, label: str, model: str, scaffold: str = "none") -> Path:
        path = self.root / f"{label}.yaml"
        path.write_text(
            CONTESTANT.format(label=label, model=model, scaffold=scaffold), encoding="utf-8"
        )
        return path

    def _fake_claude(self) -> Path:
        return write_script(
            self.root / "claude",
            f"""case "$1" in
--version) cat '{CLAUDE / "version.txt"}'; exit 0 ;;
auth) cat '{CLAUDE / "auth-status.signed-in.json"}'; exit 0 ;;
esac
echo run >> '{self.calls}'
if [ -f '{self.fail_marker}' ]; then rm '{self.fail_marker}'; exit 1; fi
echo done > answer.md
cat '{CLAUDE / "stream.synthetic.jsonl"}'
""",
        )

    def sign_in(self) -> None:
        AccountStore(self.home / "accounts").save(
            "claude", SignIn(files={".claude/.credentials.json": b"{}"}), "test"
        )

    def args(self, *contestants: Path, extra: tuple[str, ...] = ()) -> list[str]:
        args = ["run", str(self.root / "suite")]
        for contestant in contestants:
            args += ["-c", str(contestant)]
        return [
            *args,
            "--repeats",
            "1",
            "--db",
            str(self.root / "arena.db"),
            "--providers",
            str(self.root / "providers.yaml"),
            "--catalog",
            str(self.root / "models.yaml"),
            "--home",
            str(self.home),
            *extra,
        ]

    def rows(self, sql: str) -> list[sqlite3.Row]:
        with sqlite3.connect(self.root / "arena.db") as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(sql).fetchall()

    @property
    def cli_calls(self) -> int:
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    work = Workspace(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_PATH", str(work.binary))
    monkeypatch.setenv("ARENA_DISCOVER_SIGNINS", "0")
    return work


def test_mixed_mock_and_subscription_run_completes_and_records_cli_usage(
    workspace: Workspace,
) -> None:
    workspace.sign_in()

    result = CliRunner().invoke(app, workspace.args(workspace.cli, workspace.mock))

    assert result.exit_code == 0, result.output
    assert "completed=2" in result.output
    assert workspace.cli_calls == 1
    artifacts = workspace.rows("SELECT trial_id, path FROM trial_artifacts")
    assert [row["path"] for row in artifacts] == ["answer.md", "answer.md"]
    calls = workspace.rows("SELECT cost_usd, model_served, details_json FROM calls")
    assert len(calls) == 1
    assert calls[0]["cost_usd"] is None
    assert calls[0]["model_served"] == "claude-opus-5-5"
    assert json.loads(calls[0]["details_json"])["upstream"] == "cli_reported"
    assert workspace.rows("SELECT passed FROM scores WHERE passed IS NOT NULL")
    events = [
        json.loads(line)
        for path in (workspace.root / "runs").glob("*/events.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    finished = [event for event in events if event["kind"] == "call_finished"]
    assert [event["data"]["source"] for event in finished] == ["cli_reported"]


def test_rerun_does_not_call_the_cli_again(workspace: Workspace) -> None:
    workspace.sign_in()
    args = workspace.args(workspace.cli, workspace.mock)

    CliRunner().invoke(app, args)
    again = CliRunner().invoke(app, args)

    assert again.exit_code == 0, again.output
    assert "cache_hits=2" in again.output
    assert workspace.cli_calls == 1


def test_a_failed_cli_trial_errors_and_resume_runs_only_it(workspace: Workspace) -> None:
    workspace.sign_in()
    workspace.fail_marker.write_text("x")
    args = workspace.args(workspace.cli, workspace.mock)

    first = CliRunner().invoke(app, args)

    assert first.exit_code == 2
    assert "agent run failed before completion" in first.output
    run_id = workspace.rows("SELECT id FROM runs")[0]["id"]
    resumed = CliRunner().invoke(app, [*args, "--resume", run_id])
    assert resumed.exit_code == 0, resumed.output
    assert workspace.cli_calls == 2
    assert {row["status"] for row in workspace.rows("SELECT status FROM trials")} == {"succeeded"}
    assert len(workspace.rows("SELECT id FROM calls")) == 2


def test_missing_sign_in_is_refused_before_any_call(workspace: Workspace) -> None:
    result = CliRunner().invoke(app, workspace.args(workspace.cli))

    assert result.exit_code == 2
    assert "[sign_in]" in result.output
    assert workspace.cli_calls == 0
    assert not (workspace.root / "arena.db").exists()


def test_missing_cli_is_refused_and_dry_run_reports_it(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace.sign_in()
    monkeypatch.setenv("CLAUDE_CODE_PATH", str(workspace.root / "absent"))

    for extra in ((), ("--dry-run",)):
        result = CliRunner().invoke(app, workspace.args(workspace.cli, extra=extra))
        assert result.exit_code == 2
        assert "not_installed" in result.output


def test_scaffold_on_a_cli_contestant_is_refused(workspace: Workspace) -> None:
    workspace.sign_in()
    scaffolded = workspace._contestant(
        "scaffolded", "claude-plan/claude-opus-5-5", "{id: claude-code, version: 2.4.1}"
    )

    result = CliRunner().invoke(app, workspace.args(scaffolded, extra=("--dry-run",)))

    assert result.exit_code == 2
    assert "[scaffold]" in result.output


def test_subscription_model_must_be_a_catalog_subscription_entry(workspace: Workspace) -> None:
    workspace.sign_in()
    other = workspace._contestant("other", "claude-plan/claude-sonnet-9")

    result = CliRunner().invoke(app, workspace.args(other, extra=("--dry-run",)))

    assert result.exit_code == 2
    assert "[catalog]" in result.output

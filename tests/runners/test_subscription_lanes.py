"""`arena run` lanes and rests when two provider aliases share one subscription account.

The fake `claude` replays the synthetic stream and never calls a model. The two aliases point at
the same stored sign-in, so they must share one lane and one run-scoped rest.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.agents.local.types import SignIn
from arena.cli import app
from arena.providers.accounts import AccountStore, account_label
from arena.runners.account_context import account_key
from tests.providers.fakes import FIXTURES, write_script

CLAUDE = FIXTURES / "claude"
CREDENTIALS = b'{"claudeAiOauth": {"accessToken": "synthetic-not-a-real-token"}}'
PROVIDERS = """providers:
  - id: mock
    kind: mock
  - id: claude-a
    keys: []
    subscription: {vendor: claude, plan: max, import: own-sign-in, runtime: local-cli}
    limits: {concurrency: 3}
  - id: claude-b
    keys: []
    subscription: {vendor: claude, plan: max, import: own-sign-in, runtime: local-cli}
    limits: {concurrency: 1}
"""
CATALOG = """price_version: 2026-10-11
models:
  - ref: claude-a/claude-opus-5-5
    protocols: [anthropic]
    pricing: subscription
  - ref: claude-b/claude-opus-5-5
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
CONTESTANT = "id_label: {label}\nmodel: {model}\nscaffold: none\norchestration: single\n"


class AliasedWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.calls = root / "calls.txt"
        self.limit_marker = root / "limit"
        self.seen_credentials = root / "seen-credentials"
        suite = root / "suite" / "tasks" / "note"
        suite.mkdir(parents=True)
        (suite / "task.yaml").write_text(TASK, encoding="utf-8")
        (suite / "prompt.md").write_text("Say done.\n", encoding="utf-8")
        (root / "providers.yaml").write_text(PROVIDERS, encoding="utf-8")
        (root / "models.yaml").write_text(CATALOG, encoding="utf-8")
        self.a = self._contestant("a", "claude-a/claude-opus-5-5")
        self.b = self._contestant("b", "claude-b/claude-opus-5-5")
        self.binary = self._fake_claude()

    def _contestant(self, label: str, model: str) -> Path:
        path = self.root / f"{label}.yaml"
        path.write_text(CONTESTANT.format(label=label, model=model), encoding="utf-8")
        return path

    def _fake_claude(self) -> Path:
        return write_script(
            self.root / "claude",
            f"""case "$1" in
--version) cat '{CLAUDE / "version.txt"}'; exit 0 ;;
auth) cat '{CLAUDE / "auth-status.signed-in.json"}'; exit 0 ;;
esac
echo run >> '{self.calls}'
cp "$CLAUDE_CONFIG_DIR/.credentials.json" '{self.seen_credentials}'
if [ -f '{self.limit_marker}' ]; then
  echo '{{"type":"rate_limit_event","rate_limit_info":{{"status":"rejected"}}}}'
  exit 0
fi
echo done > answer.md
cat '{CLAUDE / "stream.synthetic.jsonl"}'
""",
        )

    def sign_in(self) -> None:
        AccountStore(self.home / "accounts").save(
            "claude", SignIn(files={".claude/.credentials.json": CREDENTIALS}), "test"
        )

    def args(self, *contestants: Path) -> list[str]:
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
        ]

    def rows(self, sql: str) -> list[sqlite3.Row]:
        with sqlite3.connect(self.root / "arena.db") as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(sql).fetchall()

    @property
    def cli_calls(self) -> int:
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0


@pytest.fixture
def aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AliasedWorkspace:
    work = AliasedWorkspace(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_PATH", str(work.binary))
    monkeypatch.setenv("ARENA_DISCOVER_SIGNINS", "0")
    return work


def test_aliases_of_one_account_share_a_run_scoped_rest(aliases: AliasedWorkspace) -> None:
    aliases.sign_in()
    aliases.limit_marker.write_text("x")

    result = CliRunner().invoke(app, aliases.args(aliases.a, aliases.b))

    assert result.exit_code == 2, result.output
    # One lane, so the first alias hits the limit and the second is refused by the shared rest
    # without spending quota: exactly one CLI invocation.
    assert aliases.cli_calls == 1
    calls = aliases.rows("SELECT status, error_class FROM calls")
    assert len(calls) == 1
    assert (calls[0]["status"], calls[0]["error_class"]) == (429, "subscription_limit")


def test_sign_in_is_resolved_once_and_the_same_bytes_reach_the_cli(
    aliases: AliasedWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    aliases.sign_in()
    loads: list[str] = []
    original = AccountStore.load

    def counting(self: AccountStore, vendor: str) -> SignIn | None:
        loads.append(vendor)
        return original(self, vendor)

    monkeypatch.setattr(AccountStore, "load", counting)

    result = CliRunner().invoke(app, aliases.args(aliases.a))

    assert result.exit_code == 0, result.output
    assert loads == ["claude"]  # resolved once for the whole run, not per trial or per preflight
    assert aliases.seen_credentials.read_bytes() == CREDENTIALS  # the frozen bytes, unchanged


def test_the_private_account_key_never_reaches_a_record_or_a_log(
    aliases: AliasedWorkspace, capsys: pytest.CaptureFixture[str]
) -> None:
    """The lane/rest key is a full-length hash of the sign-in; it stays in memory.

    Only the 8-character redacted label may appear in the ledger, the events or anything the
    run prints. A leaked full-length key would be a stable account identifier written to disk.
    """
    aliases.sign_in()
    sign_in = AccountStore(aliases.home / "accounts").load("claude")
    assert sign_in is not None
    private = account_key("claude", sign_in)
    assert len(private) == 64

    result = CliRunner().invoke(app, aliases.args(aliases.a))
    assert result.exit_code == 0, result.output

    printed = capsys.readouterr()
    assert private not in result.output
    assert private not in printed.out
    assert private not in printed.err
    assert private not in (aliases.root / "arena.db").read_bytes().decode("utf-8", "replace")
    events = aliases.root / "runs"
    for path in events.rglob("*") if events.exists() else []:
        if path.is_file():
            assert private not in path.read_text(encoding="utf-8", errors="replace")
    # The redacted label is what the ledger is allowed to carry.
    expected = "cli-" + account_label("claude", dict(sign_in.files))
    labels = aliases.rows("SELECT DISTINCT account_id FROM calls")
    assert [row["account_id"] for row in labels] == [expected]


@pytest.mark.parametrize("first", ["a", "b"])
def test_aliases_share_one_lane_and_both_complete_in_reverse_order(
    aliases: AliasedWorkspace, first: str
) -> None:
    """claude-a declares limit 3 and claude-b limit 1; either order must schedule both."""
    aliases.sign_in()
    ordered = (aliases.a, aliases.b) if first == "a" else (aliases.b, aliases.a)

    result = CliRunner().invoke(app, aliases.args(*ordered))

    assert result.exit_code == 0, result.output
    assert aliases.cli_calls == 2
    assert {row["status"] for row in aliases.rows("SELECT status FROM trials")} == {"succeeded"}

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.core.bundle import (
    BundleError,
    build_bundle,
    compute_stats,
    export_bundle,
    open_run,
)
from arena.core.bundle_contract import validate_bundle, validate_events_jsonl
from arena.core.cas import ArtifactStore
from arena.core.modelref import ModelRef
from arena.core.models import Call, Contestant, Scaffold, Tokens
from arena.core.store import Store
from arena.stats.pairwise import PairwiseJudgment

PRICE_VERSION = "2026-10-01"
TASKS = ("t1", "t2")
SCAFFOLD = Scaffold(id="opencode", version="1.2.0")
BASE = Contestant(label="opencode", model=ModelRef.parse("mock/echo"), scaffold=SCAFFOLD)
KIT = Contestant(
    label="opencode+kit",
    model=ModelRef.parse("mock/echo"),
    scaffold=SCAFFOLD,
    kit_hash="kit-v1",
)
DIRECT = Contestant(label="direct", model=ModelRef.parse("mock/other"))
CONTESTANTS = (BASE, KIT, DIRECT)
SCORES = {BASE.id: (0.4, 0.6), KIT.id: (0.8, 0.8), DIRECT.id: (0.2, 0.2)}
# (contestant, task, attempt) -> flag planted with a score that would change the mean
EXCLUDED = {(DIRECT.id, "t2", 1): "swapped", (DIRECT.id, "t1", 2): "unmetered"}


def _event(seq: int, run_id: str, kind: str, ref: str | None, data: dict[str, Any]) -> str:
    return json.dumps(
        {
            "event_version": 1,
            "seq": seq,
            "ts": f"2026-10-08T10:00:{seq:02d}Z",
            "run_id": run_id,
            "kind": kind,
            "ref": ref,
            "data": data,
        },
        separators=(",", ":"),
    )


def seed_run(home: Path, run_id: str = "run-1", *, shift: float = 0.0) -> None:
    """Write one finished run the way the runner, ledger and event log would."""
    store = Store(home / "arena.db")
    config = {
        "suite_id": "smoke",
        "contestants": [
            {"id": c.id, "label": c.label} | c.resolved_config() for c in CONTESTANTS
        ],
    }
    store.execute(
        "INSERT INTO runs(id, config_json, status) VALUES (?, ?, 'succeeded')",
        (run_id, json.dumps(config)),
    )
    events = [_event(1, run_id, "run_started", None, {"suite_id": "smoke"})]
    seq = 2
    call_seq = 0
    blobs = ArtifactStore(home / "artifacts")
    for contestant in CONTESTANTS:
        for task in TASKS:
            for attempt in (1, 2):
                trial_id = f"{run_id}-{contestant.id}-{task}-{attempt}"
                flag = EXCLUDED.get((contestant.id, task, attempt))
                flags = {
                    "swapped": flag == "swapped",
                    "unmetered": flag == "unmetered",
                    "subscription_served": contestant.id == DIRECT.id,
                }
                store.execute(
                    "INSERT INTO trials(id, run_id, contestant_id, task_id, attempt, status, "
                    "flags_json) VALUES (?, ?, ?, ?, ?, 'succeeded', ?)",
                    (trial_id, run_id, contestant.id, task, attempt, json.dumps(flags)),
                )
                score = 0.9 if flag else SCORES[contestant.id][attempt - 1] + shift * (task == "t1")
                store.execute(
                    "INSERT INTO scores(trial_id, scorer_id, scorer_version, value, normalized, "
                    "passed) VALUES (?, 'smoke-output', '1', ?, ?, ?)",
                    (trial_id, score, score, score >= 0.5),
                )
                call_seq += 1
                priced = contestant.id != DIRECT.id
                call = Call(
                    id=f"{trial_id}-call",
                    seq=call_seq,
                    run_id=run_id,
                    trial_id=trial_id,
                    protocol_in="openai",
                    protocol_out="openai",
                    provider="mock",
                    account_id="acct-hash",
                    model_asked="echo",
                    tokens=Tokens.model_validate({"in": 100, "out": 20}),
                    cost_usd=(0.02 if contestant.id == KIT.id else 0.01) if priced else None,
                    price_version=PRICE_VERSION if priced else None,
                    total_ms=100 * call_seq,
                )
                store.execute(
                    "INSERT INTO calls(id, seq, run_id, trial_id, provider, account_id, "
                    "model_asked, tokens_json, cost_usd, details_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        call.id,
                        call.seq,
                        run_id,
                        trial_id,
                        call.provider,
                        call.account_id,
                        call.model_asked,
                        json.dumps(call.tokens.model_dump(mode="json")),
                        call.cost_usd,
                        json.dumps(call.model_dump(mode="json")),
                    ),
                )
                events.append(_event(seq, run_id, "trial_started", trial_id, {}))
                if contestant.scaffold is not None:
                    session_id = f"{trial_id}-s"
                    turn = {
                        "call_ids": [call.id],
                        "tool_calls": [],
                        "skill_events": (
                            [{"kind": "invoked", "skill": "house-style", "kit_hash": "kit-v1"}]
                            if contestant.id == KIT.id
                            else []
                        ),
                        "mcp_calls": [],
                        "files": [],
                        "unmetered": False,
                    }
                    events.append(
                        _event(
                            seq + 1,
                            run_id,
                            "session_turn",
                            session_id,
                            {
                                "session_id": session_id,
                                "trial_id": trial_id,
                                "turn_index": 1,
                                "turn": turn,
                            },
                        )
                    )
                    seq += 1
                events.append(
                    _event(seq + 1, run_id, "trial_finished", trial_id, {"status": "succeeded"})
                )
                seq += 2
                digest = blobs.put(f"answer from {trial_id}".encode())
                store.execute(
                    "INSERT INTO trial_artifacts(trial_id, path, sha256, mime, render_hint) "
                    "VALUES (?, 'answer.txt', ?, 'text/plain', 'code')",
                    (trial_id, digest),
                )
    events.append(_event(seq, run_id, "run_finished", None, {"status": "succeeded"}))
    run_dir = home / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text("\n".join(events) + "\n", encoding="utf-8")
    store.close()


@pytest.fixture
def home(testenv: Any) -> Path:
    path = testenv.home / ".arena"
    seed_run(path)
    return path


def test_export_writes_a_bundle_that_validates_against_both_schemas(
    home: Path, tmp_path: Path
) -> None:
    records = open_run(home, "run-1")
    out = tmp_path / "bundle"

    export_bundle(records, compute_stats(records), out, home)

    bundle = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    validate_bundle(bundle)
    validate_events_jsonl((out / "events.jsonl").read_text(encoding="utf-8"))
    assert (out / "events.jsonl").read_bytes() == (home / "runs/run-1/events.jsonl").read_bytes()
    assert bundle["bundle_version"] == 2
    assert bundle["provenance"] == {
        "origin": "native",
        "verification": "verified",
        "importer": None,
        "importer_version": None,
        "source_ref": None,
    }
    assert len(bundle["trials"]) == 12
    assert len(bundle["calls"]) == 12
    assert len(bundle["artifacts"]) == 12
    assert all(artifact["trial_id"] for artifact in bundle["artifacts"])
    sessions = {s["id"]: s for s in bundle["sessions"]}
    assert len(sessions) == 8  # only the two agent contestants have sessions
    first = sessions[f"run-1-{KIT.id}-t1-1-s"]
    assert first["agent"] == "opencode"
    assert first["trial_id"] == f"run-1-{KIT.id}-t1-1"
    assert first["turns"][0]["call_ids"] == [f"run-1-{KIT.id}-t1-1-call"]
    for artifact in bundle["artifacts"]:
        assert (out / "artifacts" / artifact["sha256"]).is_file()


def test_export_includes_calls_summary_series_and_a_manifest_of_digests(
    home: Path, tmp_path: Path
) -> None:
    records = open_run(home, "run-1")
    out = tmp_path / "bundle"

    export_bundle(records, compute_stats(records), out, home)

    summary = json.loads((out / "calls-summary.json").read_text(encoding="utf-8"))
    assert summary["calls"] == 12
    assert summary["price_versions"] == [PRICE_VERSION]
    assert summary["unpriced_calls"] == 4  # the subscription contestant has no dollar cost
    assert summary["cost_usd"] == pytest.approx(4 * 0.01 + 4 * 0.02)
    assert summary["by_purpose"]["contestant"]["calls"] == 12
    series = json.loads((out / "series.json").read_text(encoding="utf-8"))
    assert {point["contestant_id"] for point in series["usage"]} == {c.id for c in CONTESTANTS}
    assert len(series["latency"]) == 3
    assert len(series["prompt_composition"]) == 12
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run-1"
    assert manifest["bundle_version"] == 2
    assert manifest["event_version"] == 1
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest
    assert set(manifest["files"]) == {
        "bundle.json",
        "calls-summary.json",
        "events.jsonl",
        "series.json",
        "stats.json",
    }


def test_stats_exclude_swapped_and_unmetered_trials_and_recover_planted_kit_lift(
    home: Path,
) -> None:
    stats = compute_stats(open_run(home, "run-1"))

    rows = {row["contestant_id"]: row for row in stats["leaderboard"]}
    assert [row["contestant_id"] for row in stats["leaderboard"]] == [KIT.id, BASE.id, DIRECT.id]
    assert rows[KIT.id]["suite"]["estimate"] == pytest.approx(0.8)
    assert rows[BASE.id]["suite"]["estimate"] == pytest.approx(0.5)
    # the planted 0.9 scores on the flagged trials would lift this to 0.55 if counted
    assert rows[DIRECT.id]["suite"]["estimate"] == pytest.approx(0.2)
    assert rows[DIRECT.id]["excluded"] == {
        "swapped": 1,
        "unmetered": 1,
        "kit_unapplied": 0,
        "total": 2,
    }
    assert rows[KIT.id]["vs_top"] is None
    assert rows[BASE.id]["vs_top"]["no_detectable_difference"] is False
    assert rows[BASE.id]["cost_usd_per_task"] == pytest.approx(0.01)
    assert rows[DIRECT.id]["cost_usd_per_task"] is None
    assert stats["pareto"]["cost_axis"] == "tokens_per_task"
    assert KIT.id in stats["pareto"]["frontier"]
    [effect] = stats["kit_effects"]
    assert (effect["baseline_id"], effect["treatment_id"]) == (BASE.id, KIT.id)
    assert effect["difference"]["estimate"] == pytest.approx(0.3)
    assert effect["status"] == "estimated"
    assert effect["uptake"]["house-style"]["invoked"] == 4
    assert stats["price_versions"] == [PRICE_VERSION]
    assert stats["ratings"] == {}


def test_stats_rate_contestants_when_pairwise_judgments_are_supplied(home: Path) -> None:
    judgments = [PairwiseJudgment(KIT.id, BASE.id, "left", "model") for _ in range(6)]
    judgments += [PairwiseJudgment(BASE.id, DIRECT.id, "left", "model") for _ in range(6)]

    stats = compute_stats(open_run(home, "run-1"), judgments=judgments)

    [component] = stats["ratings"]["model"]["leaderboards"]
    assert [row["contestant_id"] for row in component] == [KIT.id, BASE.id, DIRECT.id]
    assert "human" not in stats["ratings"]


def test_stats_diff_against_a_baseline_run_lists_the_regression(home: Path) -> None:
    seed_run(home, "run-0")
    seed_run(home, "run-2", shift=-0.3)  # every t1 score drops by 0.3

    stats = compute_stats(open_run(home, "run-2"), baseline=open_run(home, "run-0"))

    changes = [
        (row["contestant_id"], change["task_id"], change["status"])
        for row in stats["run_diff"]["contestants"]
        for change in row["tasks"]
        if change["status"] != "unchanged"
    ]
    assert (BASE.id, "t1", "regressed") in changes
    assert all(task == "t1" for _, task, _ in changes)


def test_bundle_marks_a_native_run_verified_and_an_imported_run_unverified(home: Path) -> None:
    store = Store(home / "arena.db")
    config = {
        "suite_id": "imported-suite",
        "contestants": [{"id": BASE.id, "label": "x"} | BASE.resolved_config()],
        "provenance": {
            "origin": "imported",
            "verification": "unverified",
            "importer": "inspect",
            "importer_version": "1.0",
            "source_ref": "log.json",
        },
    }
    store.execute(
        "INSERT INTO runs(id, config_json, status) VALUES ('run-imp', ?, 'succeeded')",
        (json.dumps(config),),
    )
    store.close()
    (home / "runs/run-imp").mkdir()
    (home / "runs/run-imp/events.jsonl").write_text(
        _event(1, "run-imp", "run_started", None, {}) + "\n", encoding="utf-8"
    )

    bundle = build_bundle(open_run(home, "run-imp"))

    validate_bundle(bundle)
    assert bundle["provenance"]["origin"] == "imported"
    assert bundle["provenance"]["verification"] == "unverified"


def test_cli_report_prints_markdown_with_price_version_and_exclusion_footnote(
    home: Path,
) -> None:
    result = CliRunner().invoke(app, ["report", "run-1", "--home", str(home)])

    assert result.exit_code == 0, result.output
    assert f"price_version: {PRICE_VERSION}" in result.stdout
    assert "| 1 | opencode+kit |" in result.stdout
    assert "Excluded from headline numbers" in result.stdout
    assert "direct†" in result.stdout
    assert "† direct: 1 swapped, 1 unmetered (2 trials)" in result.stdout
    assert "native (verified)" in result.stdout


def test_cli_report_says_so_when_nothing_was_excluded_or_priced(home: Path) -> None:
    store = Store(home / "arena.db")
    store.execute("DELETE FROM trials WHERE contestant_id = ?", (DIRECT.id,))
    store.execute("UPDATE calls SET cost_usd = NULL")
    store.close()

    result = CliRunner().invoke(app, ["report", "run-1", "--home", str(home)])

    assert result.exit_code == 0, result.output
    assert "price_version: " + PRICE_VERSION in result.stdout  # recorded on the calls
    assert "Excluded from headline numbers" not in result.stdout


def test_cli_export_writes_the_bundle_and_refuses_to_overwrite(home: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"

    first = CliRunner().invoke(app, ["export", "run-1", "--home", str(home), "--out", str(out)])
    second = CliRunner().invoke(app, ["export", "run-1", "--home", str(home), "--out", str(out)])

    assert first.exit_code == 0, first.output
    validate_bundle(json.loads((out / "bundle.json").read_text(encoding="utf-8")))
    assert second.exit_code == 1
    assert "already exists" in second.output


def test_cli_reports_an_unknown_run_and_a_missing_store(home: Path, tmp_path: Path) -> None:
    unknown = CliRunner().invoke(app, ["report", "nope", "--home", str(home)])
    missing = CliRunner().invoke(app, ["report", "run-1", "--home", str(tmp_path / "empty")])

    assert unknown.exit_code == 1
    assert "run not found: nope" in unknown.output
    assert missing.exit_code == 1
    assert "no arena.db" in missing.output
    assert not (tmp_path / "empty").exists()


def test_missing_event_log_is_an_error_not_an_empty_run(home: Path) -> None:
    (home / "runs/run-1/events.jsonl").unlink()

    with pytest.raises(BundleError, match="events.jsonl"):
        open_run(home, "run-1")


def test_invalid_event_log_is_an_error(home: Path) -> None:
    path = home / "runs/run-1/events.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '{"seq": 99}\n', encoding="utf-8")

    with pytest.raises(BundleError, match="events.jsonl line"):
        open_run(home, "run-1")


def test_unreadable_call_record_is_an_error_naming_the_call(home: Path) -> None:
    store = Store(home / "arena.db")
    store.execute("UPDATE calls SET details_json = '{not json' WHERE seq = 3")
    store.close()

    with pytest.raises(BundleError, match="call run-1-.*-call"):
        open_run(home, "run-1")


def test_mixed_scorer_versions_for_one_task_are_an_error(home: Path) -> None:
    store = Store(home / "arena.db")
    store.execute(
        "UPDATE scores SET scorer_version = '2' WHERE id = (SELECT min(id) FROM scores)"
    )
    store.close()

    with pytest.raises(BundleError, match="scorer_version"):
        compute_stats(open_run(home, "run-1"))


def test_missing_artifact_blob_aborts_the_export_and_leaves_no_output(
    home: Path, tmp_path: Path
) -> None:
    store = Store(home / "arena.db")
    digest = store.execute("SELECT sha256 FROM trial_artifacts LIMIT 1").fetchone()[0]
    store.close()
    (home / "artifacts" / digest).unlink()
    records = open_run(home, "run-1")
    out = tmp_path / "bundle"

    with pytest.raises(BundleError, match="artifact blob"):
        export_bundle(records, compute_stats(records), out, home)

    assert not out.exists()
    assert list(tmp_path.iterdir()) == []


def test_run_id_cannot_escape_the_runs_directory(home: Path) -> None:
    with pytest.raises(BundleError, match="run id"):
        open_run(home, "../run-1")

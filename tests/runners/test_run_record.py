from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError
from typer.testing import CliRunner

from arena.cli import app
from arena.core.bundle import build_bundle, open_run
from arena.core.bundle_contract import validate_event
from arena.core.cas import ArtifactStore
from arena.core.store import Store
from arena.runners.run_record import RunRecorder, events_path


def _tick() -> object:
    start = datetime(2026, 10, 11, tzinfo=UTC)
    count = iter(range(1000))
    return lambda: start + timedelta(seconds=next(count))


def _gateway_event(seq: int, second: int) -> dict[str, object]:
    return {
        "event_version": 1,
        "seq": seq,
        "ts": (datetime(2026, 10, 11, tzinfo=UTC) + timedelta(seconds=second)).isoformat(),
        "run_id": "r1",
        "kind": "call_queued",
        "ref": f"call-{seq}",
        "data": {},
    }


def test_write_merges_gateway_events_by_time_and_renumbers(tmp_path: Path) -> None:
    path = events_path(tmp_path, "r1")
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(json.dumps(_gateway_event(seq, second)) for seq, second in ((1, 1), (2, 3)))
        + "\n",
        encoding="utf-8",
    )
    recorder = RunRecorder("r1", clock=_tick())  # type: ignore[arg-type]
    recorder.emit("run_started", suite_id="smoke")
    recorder.emit("run_finished", status="succeeded")
    recorder.write(tmp_path)

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [event["seq"] for event in events] == [1, 2, 3, 4]
    assert [event["kind"] for event in events] == [
        "run_started",
        "call_queued",
        "run_finished",
        "call_queued",
    ]
    for event in events:
        validate_event(event)
    assert not recorder.pending


def test_write_without_gateway_events_creates_the_file(tmp_path: Path) -> None:
    recorder = RunRecorder("r1", clock=_tick())  # type: ignore[arg-type]
    recorder.emit("run_started", suite_id="smoke")
    path = recorder.write(tmp_path)
    assert path == tmp_path / "runs" / "r1" / "events.jsonl"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_write_rejects_an_invalid_event_and_keeps_the_old_file(tmp_path: Path) -> None:
    path = events_path(tmp_path, "r1")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({**_gateway_event(1, 1), "kind": "bogus"}) + "\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    recorder = RunRecorder("r1", clock=_tick())  # type: ignore[arg-type]
    recorder.emit("run_started", suite_id="smoke")
    with pytest.raises(ValidationError):
        recorder.write(tmp_path)
    assert path.read_text(encoding="utf-8") == before


def test_mock_run_is_readable_as_a_bundle(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "suites/smoke",
            "-c",
            "mock-a",
            "-c",
            "mock-b",
            "--repeats",
            "1",
            "--db",
            str(tmp_path / "arena.db"),
        ],
    )
    assert result.exit_code == 0, result.output
    run_id = result.output.split("Run ", 1)[1].split(":", 1)[0]
    assert f"run_id={run_id}" in result.output.splitlines()

    bundle = build_bundle(open_run(tmp_path, run_id))
    assert {item["label"] for item in bundle["contestants"]} == {"mock-a", "mock-b"}
    assert len(bundle["trials"]) == 8
    assert {item["path"] for item in bundle["artifacts"]} == {"answer.md"}

    kinds = [
        json.loads(line)["kind"]
        for line in events_path(tmp_path, run_id).read_text(encoding="utf-8").splitlines()
    ]
    assert kinds[0] == "run_started"
    assert kinds[-1] == "run_finished"
    assert kinds.count("trial_started") == kinds.count("trial_finished") == 8

    with Store(tmp_path / "arena.db") as store:
        blobs = ArtifactStore(tmp_path / "artifacts")
        rows = store.execute("SELECT sha256 FROM trial_artifacts").fetchall()
        assert len(rows) == 8
        assert all(blobs.get(row["sha256"]) for row in rows)

    report = runner.invoke(app, ["report", run_id, "--home", str(tmp_path)])
    assert report.exit_code == 0, report.output
    assert "mock-a" in report.output
    exported = runner.invoke(
        app, ["export", run_id, "--home", str(tmp_path), "--out", str(tmp_path / "bundle")]
    )
    assert exported.exit_code == 0, exported.output

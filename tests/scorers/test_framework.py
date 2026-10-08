from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from arena.core.store import Store
from typer.testing import CliRunner

from arena.cli_score import create_score_command
from arena.scorers.base import Score, ScorerContext, ScorerError, aggregate_scores
from arena.scorers.registry import ScorerRegistry


class FixedScorer:
    def __init__(self, scorer_id: str = "quality", version: str = "1") -> None:
        self.id = scorer_id
        self.version = version
        self.calls: list[ScorerContext] = []

    def score(self, context: ScorerContext) -> Score:
        self.calls.append(context)
        return Score(
            trial_id=context.trial_id,
            scorer_id=self.id,
            scorer_version=self.version,
            value=4,
            normalized=0.8,
            passed=True,
            rationale="deterministic result",
            evidence={"check": "fixture", "artifact_count": len(context.artifacts)},
        )


def test_score_contract_and_weighted_aggregation() -> None:
    build = Score(
        trial_id="t1",
        scorer_id="build",
        scorer_version="2",
        value=1,
        normalized=0.9,
        passed=True,
        evidence={"tests": ["unit"]},
    )
    rubric = Score(
        trial_id="t1",
        scorer_id="rubric",
        scorer_version="3",
        value=4,
        normalized=0.8,
        passed=True,
    )
    assert build.scorer_ref == "build@2"
    assert aggregate_scores([build, rubric], {"build": 1, "rubric@3": 3}) == pytest.approx(0.825)


def test_failed_gate_caps_aggregate_at_point_three() -> None:
    scores = [
        Score(
            trial_id="t1",
            scorer_id="build",
            scorer_version="1",
            value=0,
            normalized=0,
            passed=False,
        ),
        Score(
            trial_id="t1",
            scorer_id="rubric",
            scorer_version="1",
            value=5,
            normalized=1,
            passed=True,
        ),
    ]
    assert aggregate_scores(scores, gates={"build"}) == 0.3


def test_registry_requires_explicit_version_when_ambiguous() -> None:
    registry = ScorerRegistry([FixedScorer(version="1"), FixedScorer(version="2")])
    with pytest.raises(ScorerError, match="multiple versions"):
        registry.get("quality")
    assert registry.get("quality@1").version == "1"
    assert registry.versions("quality") == ("1", "2")


def test_aggregate_rejects_mixed_versions() -> None:
    scores = [
        Score(trial_id="t1", scorer_id="quality", scorer_version=version, value=1, normalized=0.5)
        for version in ("1", "2")
    ]
    with pytest.raises(ScorerError, match="mixed scorer versions"):
        aggregate_scores(scores)


def test_arena_score_reruns_only_scoring_and_upserts_versioned_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "arena.db"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    artifact_bytes = b"saved trial output"
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    (artifact_root / digest).write_bytes(artifact_bytes)
    scorer_v1 = FixedScorer(version="1")
    registry = ScorerRegistry([scorer_v1, FixedScorer(version="2")])

    with Store(db_path) as store:
        store.execute(
            "INSERT INTO runs (id, config_json, status) VALUES (?, ?, ?)",
            ("run-1", json.dumps({"scorers": ["quality@1"]}), "finished"),
        )
        store.execute(
            "INSERT INTO trials (id, run_id, contestant_id, task_id, status) VALUES (?, ?, ?, ?, ?)",
            ("trial-1", "run-1", "contestant", "task", "succeeded"),
        )
        store.execute(
            "CREATE TABLE artifacts (trial_id TEXT NOT NULL, path TEXT NOT NULL, mime TEXT NOT NULL, "
            "render_hint TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY (trial_id, path))"
        )
        store.execute(
            "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?)",
            ("trial-1", "answer.txt", "text/plain", "code", digest),
        )
        runner = CliRunner()
        command = create_score_command(registry, db_path, artifact_root)
        first = runner.invoke(command, ["run-1"])
        second = runner.invoke(command, ["--scorer", "quality@2", "run-1"])
        assert first.exit_code == 0, first.output
        assert second.exit_code == 0, second.output
        assert "Scored 1 result(s)" in first.output
        rows = store.execute(
            "SELECT scorer_id, scorer_version, normalized FROM scores ORDER BY scorer_version"
        ).fetchall()
        assert [(row["scorer_id"], row["scorer_version"], row["normalized"]) for row in rows] == [
            ("quality", "1", 0.8),
            ("quality", "2", 0.8),
        ]
    assert len(scorer_v1.calls) == 1
    assert scorer_v1.calls[0].artifacts["answer.txt"]["path"] == artifact_root / digest


def test_score_command_missing_artifact_is_an_error(tmp_path: Path) -> None:
    db_path = tmp_path / "arena.db"
    scorer = FixedScorer()
    registry = ScorerRegistry([scorer])
    with Store(db_path) as store:
        store.execute(
            "INSERT INTO runs (id, config_json, status) VALUES (?, ?, ?)",
            ("run-1", '{"scorers":["quality@1"]}', "finished"),
        )
        store.execute(
            "INSERT INTO trials (id, run_id, contestant_id, task_id, status) VALUES (?, ?, ?, ?, ?)",
            ("trial-1", "run-1", "contestant", "task", "succeeded"),
        )
        store.execute(
            "CREATE TABLE artifacts (trial_id TEXT NOT NULL, path TEXT NOT NULL, mime TEXT NOT NULL, "
            "render_hint TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY (trial_id, path))"
        )
        store.execute(
            "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?)",
            ("trial-1", "answer.txt", "text/plain", "code", "missing-hash"),
        )
        command = create_score_command(registry, db_path, tmp_path / "artifacts")
        result = CliRunner().invoke(command, ["run-1"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ScorerError)
    assert "missing stored artifact" in str(result.exception)

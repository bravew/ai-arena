from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli_score import create_score_command, score_run
from arena.core.cas import ArtifactStore
from arena.core.models import Artifact, Score
from arena.core.store import Store
from arena.scorers.base import ScorerContext, ScorerError, aggregate_scores, scorer_ref
from arena.scorers.registry import ScorerRegistry


class FixedScorer:
    def __init__(
        self, scorer_id: str = "quality", version: str = "1", normalized: float = 0.8
    ) -> None:
        self.id = scorer_id
        self.version = version
        self.normalized = normalized
        self.calls: list[ScorerContext] = []

    def score(self, context: ScorerContext) -> Score:
        self.calls.append(context)
        return Score(
            trial_id=context.trial_id,
            scorer_id=self.id,
            scorer_version=self.version,
            value=4,
            normalized=self.normalized,
            passed=True,
            rationale="deterministic result",
            evidence={"check": "fixture", "artifact_count": len(context.artifacts)},
        )


class MismatchedScorer(FixedScorer):
    def score(self, context: ScorerContext) -> Score:
        return super().score(context).model_copy(update={"trial_id": "someone-else"})


class StaticIndex:
    def __init__(self, by_trial: Mapping[str, Sequence[Artifact]]) -> None:
        self.by_trial = by_trial

    def for_trial(self, trial_id: str) -> Sequence[Artifact]:
        return self.by_trial.get(trial_id, ())


def _score(scorer_id: str, version: str, normalized: float, passed: bool | None = True) -> Score:
    return Score(
        trial_id="t1",
        scorer_id=scorer_id,
        scorer_version=version,
        value=normalized,
        normalized=normalized,
        passed=passed,
    )


def _seed_run(store: Store, *trial_ids: str) -> None:
    store.execute(
        "INSERT INTO runs (id, config_json, status) VALUES (?, ?, ?)", ("run-1", "{}", "finished")
    )
    for trial_id in trial_ids:
        store.execute(
            "INSERT INTO trials (id, run_id, contestant_id, task_id, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (trial_id, "run-1", "contestant", f"task-{trial_id}", "succeeded"),
        )


def _artifact(blobs: ArtifactStore, data: bytes, path: str = "answer.txt") -> Artifact:
    return Artifact(sha256=blobs.put(data), path=path, mime="text/plain", render_hint="code")


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
    rubric = _score("rubric", "3", 0.8)
    assert scorer_ref(build) == "build@2"
    assert aggregate_scores([build, rubric], {"build": 1, "rubric@3": 3}) == pytest.approx(0.825)


def test_failed_gate_caps_aggregate_at_point_three() -> None:
    scores = [_score("build", "1", 0, passed=False), _score("rubric", "1", 1)]
    assert aggregate_scores(scores, gates={"build"}) == 0.3


def test_passed_gate_does_not_cap() -> None:
    scores = [_score("build", "1", 1), _score("rubric", "1", 1)]
    assert aggregate_scores(scores, gates={"build"}) == 1.0


def test_gate_without_a_verdict_is_an_error_not_a_pass() -> None:
    scores = [_score("build", "1", 1, passed=None), _score("rubric", "1", 1)]
    with pytest.raises(ScorerError, match="gate build@1 must report passed"):
        aggregate_scores(scores, gates={"build"})


def test_registry_requires_explicit_version_when_ambiguous() -> None:
    registry = ScorerRegistry([FixedScorer(version="1"), FixedScorer(version="2")])
    with pytest.raises(ScorerError, match="multiple versions"):
        registry.get("quality")
    assert registry.get("quality@1").version == "1"
    assert registry.versions("quality") == ("1", "2")


def test_aggregate_rejects_mixed_versions() -> None:
    scores = [_score("quality", version, 0.5) for version in ("1", "2")]
    with pytest.raises(ScorerError, match="mixed scorer versions"):
        aggregate_scores(scores)


def test_arena_score_reruns_only_scoring_and_upserts_versioned_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "arena.db"
    blobs = ArtifactStore(tmp_path / "artifacts")
    artifact = _artifact(blobs, b"saved trial output")
    index = StaticIndex({"trial-1": [artifact]})
    scorer_v1 = FixedScorer(version="1")
    registry = ScorerRegistry([scorer_v1, FixedScorer(version="2", normalized=0.5)])

    with Store(db_path) as store:
        _seed_run(store, "trial-1")
    command = create_score_command(registry, index, db_path, blobs.root)
    runner = CliRunner()
    first = runner.invoke(command, ["run-1", "--scorer", "quality@1"])
    second = runner.invoke(command, ["run-1", "--scorer", "quality@2"])
    again = runner.invoke(command, ["run-1", "--scorer", "quality@1"])

    for result in (first, second, again):
        assert result.exit_code == 0, result.output
    assert "Scored 1 result(s)" in first.output
    with Store(db_path) as store:
        rows = store.execute(
            "SELECT scorer_id, scorer_version, normalized, passed, evidence_json FROM scores "
            "ORDER BY scorer_version"
        ).fetchall()
    assert [(r["scorer_id"], r["scorer_version"], r["normalized"], r["passed"]) for r in rows] == [
        ("quality", "1", 0.8, 1),
        ("quality", "2", 0.5, 1),
    ]
    assert rows[0]["evidence_json"] == '{"artifact_count":1,"check":"fixture"}'
    assert len(scorer_v1.calls) == 2
    assert scorer_v1.calls[0].read("answer.txt") == b"saved trial output"


def test_score_run_fails_when_the_artifact_blob_is_missing(tmp_path: Path) -> None:
    blobs = ArtifactStore(tmp_path / "artifacts")
    missing = Artifact(sha256="a" * 64, path="answer.txt", mime="text/plain", render_hint="code")
    scorer = FixedScorer()
    with Store(tmp_path / "arena.db") as store:
        _seed_run(store, "trial-1")
        with pytest.raises(ScorerError, match="missing stored artifact"):
            score_run(
                "run-1",
                store,
                blobs,
                StaticIndex({"trial-1": [missing]}),
                ScorerRegistry([scorer]),
                scorer_refs=["quality@1"],
            )
        assert store.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0
    assert scorer.calls == []


def test_score_run_rejects_corrupt_artifact_content(tmp_path: Path) -> None:
    blobs = ArtifactStore(tmp_path / "artifacts")
    artifact = _artifact(blobs, b"saved trial output")
    blobs.path_for(artifact.sha256).write_bytes(b"tampered content")
    scorer = FixedScorer()
    with Store(tmp_path / "arena.db") as store:
        _seed_run(store, "trial-1")
        with pytest.raises(ScorerError, match="does not match its digest"):
            score_run(
                "run-1",
                store,
                blobs,
                StaticIndex({"trial-1": [artifact]}),
                ScorerRegistry([scorer]),
                scorer_refs=["quality@1"],
            )
        assert store.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0
    assert scorer.calls == []


def test_score_run_writes_nothing_when_a_later_scorer_fails(tmp_path: Path) -> None:
    blobs = ArtifactStore(tmp_path / "artifacts")
    registry = ScorerRegistry([FixedScorer(), MismatchedScorer(scorer_id="bad")])
    with Store(tmp_path / "arena.db") as store:
        _seed_run(store, "trial-1", "trial-2")
        with pytest.raises(ScorerError, match="returned trial"):
            score_run(
                "run-1",
                store,
                blobs,
                StaticIndex({}),
                registry,
                scorer_refs=["quality@1", "bad@1"],
            )
        assert store.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("run_id", "refs", "message"),
    [
        ("nope", ["quality@1"], "unknown run"),
        ("run-1", [], "at least one scorer"),
        ("run-1", ["quality@9"], "unknown scorer"),
    ],
)
def test_score_run_rejects_bad_requests(
    tmp_path: Path, run_id: str, refs: list[str], message: str
) -> None:
    with Store(tmp_path / "arena.db") as store:
        _seed_run(store, "trial-1")
        with pytest.raises(ScorerError, match=message):
            score_run(
                run_id,
                store,
                ArtifactStore(tmp_path / "artifacts"),
                StaticIndex({}),
                ScorerRegistry([FixedScorer()]),
                scorer_refs=refs,
            )


def test_score_run_reports_a_run_without_completed_trials(tmp_path: Path) -> None:
    with Store(tmp_path / "arena.db") as store:
        _seed_run(store)
        with pytest.raises(ScorerError, match="no completed trials"):
            score_run(
                "run-1",
                store,
                ArtifactStore(tmp_path / "artifacts"),
                StaticIndex({}),
                ScorerRegistry([FixedScorer()]),
                scorer_refs=["quality@1"],
            )

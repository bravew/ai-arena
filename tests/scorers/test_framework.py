from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from arena.cli import app
from arena.cli_score import StoreArtifactIndex, create_score_command, score_run
from arena.core.cas import ArtifactStore
from arena.core.models import Artifact, RenderHint, Score
from arena.core.store import Store
from arena.scorers.base import (
    ArtifactIndex,
    ScorerContext,
    ScorerError,
    aggregate_scores,
    scorer_ref,
)
from arena.scorers.registry import ScorerRegistry, default_registry
from arena.scorers.visual import PageCapture, Viewport


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


def _score_app(
    registry: ScorerRegistry, index_factory: Callable[[Store], ArtifactIndex] | None = None
) -> typer.Typer:
    """The `score` command on its own, with a test registry (and optionally a fake index)."""
    command = typer.Typer(add_completion=False)
    command.callback()(lambda: None)  # keep `score` a named subcommand, as it is in `arena`
    if index_factory is None:
        command.command("score")(create_score_command(registry))
    else:
        command.command("score")(create_score_command(registry, index_factory=index_factory))
    return command


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
    command = _score_app(registry, lambda _store: index)
    runner = CliRunner()
    home = ["--home", str(tmp_path)]
    first = runner.invoke(command, ["score", "run-1", "--scorer", "quality@1", *home])
    second = runner.invoke(command, ["score", "run-1", "--scorer", "quality@2", *home])
    again = runner.invoke(command, ["score", "run-1", "--scorer", "quality@1", *home])

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


def _insert_row(
    store: Store,
    blobs: ArtifactStore,
    trial_id: str,
    data: bytes,
    path: str = "index.html",
    mime: str = "text/html",
    render_hint: str = "html-sandbox",
) -> str:
    """Store a blob and its `trial_artifacts` row the way the runner does; return the digest."""
    digest = blobs.put(data)
    store.execute(
        "INSERT INTO trial_artifacts (trial_id, path, sha256, mime, render_hint) "
        "VALUES (?, ?, ?, ?, ?)",
        (trial_id, path, digest, mime, render_hint),
    )
    return digest


def _record_artifact(
    store: Store,
    blobs: ArtifactStore,
    trial_id: str,
    data: bytes,
    path: str = "index.html",
    mime: str = "text/html",
    render_hint: RenderHint = "html-sandbox",
) -> Artifact:
    digest = _insert_row(store, blobs, trial_id, data, path, mime, render_hint)
    return Artifact(sha256=digest, path=path, mime=mime, render_hint=render_hint, trial_id=trial_id)


def test_store_artifact_index_reads_trial_artifacts_rows(tmp_path: Path) -> None:
    blobs = ArtifactStore(tmp_path / "artifacts")
    with Store(tmp_path / "arena.db") as store:
        _seed_run(store, "trial-1", "trial-2", "trial-3")
        b = _record_artifact(store, blobs, "trial-1", b"b", path="b.txt", render_hint="code")
        a = _record_artifact(store, blobs, "trial-1", b"a", path="a.txt", render_hint="code")
        other = _record_artifact(
            store, blobs, "trial-2", b"other", path="a.txt", render_hint="code"
        )
        index = StoreArtifactIndex(store)
        assert list(index.for_trial("trial-1")) == [a, b]
        assert list(index.for_trial("trial-2")) == [other]
        assert list(index.for_trial("trial-3")) == []


def test_store_artifact_index_rejects_a_row_it_cannot_read(tmp_path: Path) -> None:
    with Store(tmp_path / "arena.db") as store:
        _seed_run(store, "trial-1")
        _insert_row(
            store, ArtifactStore(tmp_path / "artifacts"), "trial-1", b"x", render_hint="hologram"
        )
        with pytest.raises(ScorerError, match=r"(?s)index\.html.*trial-1.*render_hint"):
            StoreArtifactIndex(store).for_trial("trial-1")


class FakeChromium:
    """Stands in for Playwright in the default registry's `visual@1`."""

    def capture(self, url: str, viewport: Viewport) -> PageCapture:
        return PageCapture(png=b"\x89PNG-" + viewport.name.encode(), console_errors=())


@pytest.fixture
def fake_chromium(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("arena.scorers.visual.PlaywrightBrowser", FakeChromium)


def test_arena_score_rescores_a_run_from_its_stored_artifacts(
    tmp_path: Path, fake_chromium: None
) -> None:
    """The public entry: `arena score <run>` over a real store and blob directory."""
    html = b"<!doctype html><h1>Hello</h1>"
    blobs = ArtifactStore(tmp_path / "artifacts")
    with Store(tmp_path / "arena.db") as store:
        _seed_run(store, "trial-1", "trial-2")
        _record_artifact(store, blobs, "trial-1", html)
        # trial-2 produced no HTML, so its score is a failing one rather than a skip.

    result = CliRunner().invoke(
        app, ["score", "run-1", "--scorer", "visual@1", "--home", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert "Scored 2 result(s) for run run-1." in result.output
    with Store(tmp_path / "arena.db") as store:
        rows = store.execute(
            "SELECT trial_id, scorer_id, scorer_version, normalized, passed FROM scores "
            "ORDER BY trial_id"
        ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("trial-1", "visual", "1", 1.0, 1),
        ("trial-2", "visual", "1", 0.0, 0),
    ]
    assert blobs.contains(hashlib.sha256(b"\x89PNG-mobile").hexdigest())


def test_arena_score_reads_the_home_from_arena_home(
    tmp_path: Path, fake_chromium: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs = ArtifactStore(tmp_path / "artifacts")
    with Store(tmp_path / "arena.db") as store:
        _seed_run(store, "trial-1")
        _record_artifact(store, blobs, "trial-1", b"<h1>Hi</h1>")
    monkeypatch.setenv("ARENA_HOME", str(tmp_path))

    result = CliRunner().invoke(app, ["score", "run-1", "--scorer", "visual"])

    assert result.exit_code == 0, result.output


def test_arena_score_fails_when_a_stored_blob_does_not_match_its_digest(
    tmp_path: Path, fake_chromium: None
) -> None:
    blobs = ArtifactStore(tmp_path / "artifacts")
    with Store(tmp_path / "arena.db") as store:
        _seed_run(store, "trial-1")
        artifact = _record_artifact(store, blobs, "trial-1", b"<h1>Hello</h1>")
    blobs.path_for(artifact.sha256).write_bytes(b"<h1>tampered</h1>")

    result = CliRunner().invoke(
        app, ["score", "run-1", "--scorer", "visual@1", "--home", str(tmp_path)]
    )

    assert result.exit_code == 1
    assert "does not match its digest" in result.output
    assert "Traceback" not in result.output
    with Store(tmp_path / "arena.db") as store:
        assert store.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0


def test_score_run_raises_on_a_digest_mismatch_with_a_store_backed_index(tmp_path: Path) -> None:
    blobs = ArtifactStore(tmp_path / "artifacts")
    scorer = FixedScorer()
    with Store(tmp_path / "arena.db") as store:
        _seed_run(store, "trial-1")
        artifact = _record_artifact(
            store, blobs, "trial-1", b"saved output", path="a.txt", render_hint="code"
        )
        blobs.path_for(artifact.sha256).write_bytes(b"tampered")
        with pytest.raises(ScorerError, match="does not match its digest"):
            score_run(
                "run-1",
                store,
                blobs,
                StoreArtifactIndex(store),
                ScorerRegistry([scorer]),
                scorer_refs=["quality@1"],
            )
    assert scorer.calls == []


def test_rescoring_replaces_the_row_and_a_new_version_is_a_separate_series(
    tmp_path: Path,
) -> None:
    blobs = ArtifactStore(tmp_path / "artifacts")
    with Store(tmp_path / "arena.db") as store:
        _seed_run(store, "trial-1")
        _record_artifact(store, blobs, "trial-1", b"saved output", path="a.txt", render_hint="code")
    runner = CliRunner()
    args = ["score", "run-1", "--home", str(tmp_path)]

    def rows() -> list[tuple[str, str, float]]:
        with Store(tmp_path / "arena.db") as store:
            found = store.execute(
                "SELECT scorer_id, scorer_version, normalized FROM scores "
                "ORDER BY scorer_id, scorer_version"
            ).fetchall()
        return [(r["scorer_id"], r["scorer_version"], r["normalized"]) for r in found]

    first = _score_app(ScorerRegistry([FixedScorer(normalized=0.8)]))
    assert runner.invoke(first, [*args, "--scorer", "quality@1"]).exit_code == 0
    assert rows() == [("quality", "1", 0.8)]

    # The scorer's behaviour changed under the same version: the row is replaced, not added.
    changed = _score_app(ScorerRegistry([FixedScorer(normalized=0.4)]))
    assert runner.invoke(changed, [*args, "--scorer", "quality@1"]).exit_code == 0
    assert rows() == [("quality", "1", 0.4)]

    # A new version starts its own series and leaves version 1 alone.
    bumped = _score_app(
        ScorerRegistry([FixedScorer(normalized=0.4), FixedScorer(version="2", normalized=0.9)])
    )
    assert runner.invoke(bumped, [*args, "--scorer", "quality@2"]).exit_code == 0
    assert rows() == [("quality", "1", 0.4), ("quality", "2", 0.9)]


def test_arena_score_without_a_store_does_not_create_one(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["score", "run-1", "--scorer", "visual@1", "--home", str(tmp_path / "nowhere")]
    )

    assert result.exit_code == 1
    assert "no arena.db" in result.output
    assert not (tmp_path / "nowhere").exists()


def test_arena_score_reports_scorer_errors_without_a_traceback(tmp_path: Path) -> None:
    with Store(tmp_path / "arena.db") as store:
        _seed_run(store, "trial-1")

    result = CliRunner().invoke(
        app, ["score", "run-1", "--scorer", "nope@1", "--home", str(tmp_path)]
    )

    assert result.exit_code == 1
    assert "unknown scorer 'nope@1'" in result.output
    assert "Traceback" not in result.output


def test_default_registry_offers_the_visual_scorer() -> None:
    assert default_registry().get("visual").version == "1"

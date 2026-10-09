"""Re-score stored trial artifacts without executing trials again."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from arena.core.cas import ArtifactStore
from arena.core.models import Score
from arena.core.store import Store
from arena.scorers.base import (
    ArtifactIndex,
    ScorerContext,
    ScorerError,
    read_artifact,
    validate_score,
)
from arena.scorers.registry import ScorerRegistry


def score_run(
    run_id: str,
    store: Store,
    blobs: ArtifactStore,
    index: ArtifactIndex,
    registry: ScorerRegistry,
    *,
    scorer_refs: Sequence[str],
) -> list[Score]:
    """Score a run's finished trials from stored artifacts and upsert the results.

    Trial execution is never invoked here. Every scorer reference is resolved and
    every artifact is read and verified against its digest before any score is
    written, and all rows are written in one transaction, so a failure leaves the
    existing scores untouched.
    """
    if not scorer_refs:
        raise ScorerError("name at least one scorer to run")
    if store.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone() is None:
        raise ScorerError(f"unknown run {run_id!r}")
    trials = store.execute(
        "SELECT id FROM trials WHERE run_id = ? AND status IN ('succeeded', 'failed') ORDER BY id",
        (run_id,),
    ).fetchall()
    if not trials:
        raise ScorerError(f"run {run_id!r} has no completed trials to score")
    scorers = tuple(registry.get(ref) for ref in scorer_refs)

    results: list[Score] = []
    for trial_row in trials:
        trial_id = str(trial_row["id"])
        artifacts = {artifact.path: artifact for artifact in index.for_trial(trial_id)}
        for artifact in artifacts.values():
            read_artifact(blobs, artifact, trial_id)
        context = ScorerContext(trial_id=trial_id, artifacts=artifacts, blobs=blobs)
        for scorer in scorers:
            results.append(validate_score(scorer, context, scorer.score(context)))

    with store.transaction() as conn:
        for score in results:
            _save_score(conn, score)
    return results


def _save_score(conn: sqlite3.Connection, score: Score) -> None:
    evidence_json = json.dumps(score.evidence, sort_keys=True, separators=(",", ":"))
    conn.execute(
        "INSERT INTO scores (trial_id, scorer_id, scorer_version, value, normalized, passed, "
        "rationale, evidence_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(trial_id, scorer_id, scorer_version) DO UPDATE SET "
        "value=excluded.value, normalized=excluded.normalized, passed=excluded.passed, "
        "rationale=excluded.rationale, evidence_json=excluded.evidence_json",
        (
            score.trial_id,
            score.scorer_id,
            score.scorer_version,
            score.value,
            score.normalized,
            None if score.passed is None else int(score.passed),
            score.rationale,
            evidence_json,
        ),
    )


def create_score_command(
    registry: ScorerRegistry,
    index: ArtifactIndex,
    store_path: Path,
    artifact_root: Path,
) -> typer.Typer:
    """Build the score command with its runtime registry, artifact index and paths."""
    command = typer.Typer(
        name="score",
        help="Re-score stored trial artifacts.",
        add_completion=False,
    )

    @command.command()
    def run_score(
        run_id: Annotated[str, typer.Argument(help="Run ID to score.")],
        scorers: Annotated[
            list[str],
            typer.Option("--scorer", help="Scorer ID or exact scorer_id@version; repeatable."),
        ],
    ) -> None:
        with Store(store_path) as store:
            results = score_run(
                run_id,
                store,
                ArtifactStore(artifact_root),
                index,
                registry,
                scorer_refs=scorers,
            )
        typer.echo(f"Scored {len(results)} result(s) for run {run_id}.")

    return command

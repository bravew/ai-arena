"""Re-score stored trial artifacts without executing trials again."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from arena.core.cas import ArtifactStore
from arena.core.models import Artifact, Score
from arena.core.store import Store, StoreError
from arena.scorers.base import (
    ArtifactIndex,
    ScorerContext,
    ScorerError,
    read_artifact,
    validate_score,
)
from arena.scorers.registry import ScorerRegistry


class StoreArtifactIndex:
    """The ``trial_artifacts`` rows the runner recorded, as an `ArtifactIndex`.

    A trial with no rows has no artifacts. A row that does not parse is an error, not a
    missing artifact, so a scorer never sees a quietly shortened list.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    def for_trial(self, trial_id: str) -> Sequence[Artifact]:
        rows = self._store.execute(
            "SELECT path, sha256, mime, render_hint FROM trial_artifacts "
            "WHERE trial_id = ? ORDER BY path",
            (trial_id,),
        ).fetchall()
        artifacts: list[Artifact] = []
        for row in rows:
            try:
                artifacts.append(Artifact.model_validate({**dict(row), "trial_id": trial_id}))
            except ValidationError as exc:
                raise ScorerError(
                    f"unreadable artifact record {row['path']!r} for trial {trial_id}: {exc}"
                ) from exc
        return artifacts


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
    *,
    index_factory: Callable[[Store], ArtifactIndex] = StoreArtifactIndex,
) -> Callable[..., None]:
    """Build the ``score`` command function, to be registered on a Typer app.

    The store is ``<home>/arena.db`` and the blobs are ``<home>/artifacts``. The command
    never creates a store: scoring a home that has none is an error.
    """

    def score(
        run_id: Annotated[str, typer.Argument(help="Run ID to score.")],
        scorers: Annotated[
            list[str],
            typer.Option("--scorer", help="Scorer ID or exact scorer_id@version; repeatable."),
        ],
        home: Annotated[
            Path,
            typer.Option(
                "--home",
                envvar="ARENA_HOME",
                help="Directory holding arena.db and artifacts/.",
            ),
        ] = Path("."),
    ) -> None:
        """Re-score a run's stored artifacts. No trial is run again."""
        store_path = home / "arena.db"
        try:
            if not store_path.is_file():
                raise ScorerError(f"no arena.db in {home}")
            with Store(store_path) as store:
                results = score_run(
                    run_id,
                    store,
                    ArtifactStore(home / "artifacts"),
                    index_factory(store),
                    registry,
                    scorer_refs=scorers,
                )
        except (ScorerError, StoreError) as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(f"Scored {len(results)} result(s) for run {run_id}.")

    return score

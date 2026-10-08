"""Re-score stored trial artifacts without executing trials again."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any

import typer
from arena.core.store import Store

from arena.scorers.base import Score, ScorerContext, ScorerError, validate_score
from arena.scorers.registry import ScorerRegistry

app = typer.Typer(name="score", help="Re-score a run from stored trial artifacts.", add_completion=False)


def score_run(
    run_id: str,
    store: Store,
    artifact_root: Path,
    registry: ScorerRegistry,
    *,
    scorer_refs: Sequence[str] | None = None,
) -> list[Score]:
    """Score trials from persisted artifacts and upsert version-specific results.

    Trial execution is never invoked here. Artifact metadata is resolved from
    the content-addressed store and scorer references are pinned before writing.
    """
    trials = store.execute(
        "SELECT id FROM trials WHERE run_id = ? AND status IN ('succeeded', 'failed') ORDER BY id",
        (run_id,),
    ).fetchall()
    if not trials:
        raise ScorerError(f"run {run_id!r} has no completed trials to score")

    selected_refs = tuple(scorer_refs or ())
    if not selected_refs:
        row = store.execute("SELECT config_json FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ScorerError(f"unknown run {run_id!r}")
        try:
            config = json.loads(row["config_json"])
            selected_refs = tuple(config["scorers"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ScorerError(f"run {run_id!r} has no valid scorer list in its stored config") from exc
    scorers = tuple(registry.get(ref) for ref in selected_refs)

    results: list[Score] = []
    for trial_row in trials:
        trial_id = str(trial_row["id"])
        artifact_rows = store.execute(
            "SELECT path, mime, render_hint, sha256 FROM artifacts WHERE trial_id = ? ORDER BY path",
            (trial_id,),
        ).fetchall()
        artifacts: dict[str, Any] = {}
        for artifact in artifact_rows:
            path = artifact_root / str(artifact["sha256"])
            if not path.is_file():
                raise ScorerError(f"missing stored artifact {artifact['sha256']} for trial {trial_id}")
            artifacts[str(artifact["path"])] = {
                "path": path,
                "mime": str(artifact["mime"]),
                "render_hint": str(artifact["render_hint"]),
                "sha256": str(artifact["sha256"]),
            }

        context = ScorerContext(trial_id=trial_id, artifacts=artifacts)
        for scorer in scorers:
            score = validate_score(scorer, context, scorer.score(context))
            _save_score(store, score)
            results.append(score)
    return results


def _save_score(store: Store, score: Score) -> None:
    evidence_json = json.dumps(dict(score.evidence), sort_keys=True, separators=(",", ":"))
    store.execute(
        "INSERT INTO scores (trial_id, scorer_id, scorer_version, value, normalized, passed, rationale, evidence_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
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
    store_path: Path,
    artifact_root: Path,
) -> typer.Typer:
    """Build the score command with its runtime registry and storage paths."""
    command = typer.Typer(
        name="score",
        help="Re-score stored trial artifacts.",
        add_completion=False,
        invoke_without_command=True,
    )

    @command.callback(invoke_without_command=True)
    def run_score(
        run_id: Annotated[str, typer.Argument(help="Run ID to score.")],
        scorers: Annotated[
            list[str] | None,
            typer.Option("--scorer", help="Scorer ID or exact scorer_id@version; repeatable."),
        ] = None,
    ) -> None:
        with Store(store_path) as store:
            results = score_run(
                run_id,
                store,
                artifact_root,
                registry,
                scorer_refs=scorers,
            )
        typer.echo(f"Scored {len(results)} result(s) for run {run_id}.")

    return command


# Keep the persistence boundary explicit for implementations and test doubles.
StoreLike = Store
ArtifactMap = Mapping[str, Any]

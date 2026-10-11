"""Judge calibration report command."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from arena.cli import app
from arena.judges.calibration_report import (
    GoldSetError,
    build_reports,
    disagreements,
    load_gold_set,
)

judges_app = typer.Typer(name="judges", help="Judge evaluation commands.", no_args_is_help=True)


def report(
    gold_set: Annotated[
        Path, typer.Argument(help="JSONL file or directory containing pair_judgments.jsonl.")
    ],
    judge: Annotated[str | None, typer.Option("--judge", help="Show one judge only.")] = None,
    threshold: Annotated[
        float, typer.Option("--threshold", help="Minimum Cohen's kappa to mark calibrated.")
    ] = 0.6,
    disagreement_limit: Annotated[
        int, typer.Option("--disagreements", help="List this many largest disagreements.")
    ] = 10,
    json_output: Annotated[bool, typer.Option("--json", help="Write the report as JSON.")] = False,
) -> None:
    """Print judge calibration metrics and the largest judge/human disagreements."""
    try:
        rows = load_gold_set(gold_set)
        available = {row.judge_id for row in rows}
        if judge is not None and judge not in available:
            raise GoldSetError(
                f"unknown judge {judge!r}; available: {', '.join(sorted(available))}"
            )
        reports = build_reports(rows, threshold=threshold)
        if judge is not None:
            reports = tuple(item for item in reports if item.calibration.judge_id == judge)
        judge_ids = (judge,) if judge is not None else tuple(sorted(available))
        ranked_disagreements = [
            {
                "judge_id": judge_id,
                **asdict(row),
            }
            for judge_id in judge_ids
            for row in disagreements(rows, judge_id=judge_id, limit=disagreement_limit)
        ]
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "threshold": threshold,
                        "judges": [
                            {**asdict(item.calibration), "status": item.status.value}
                            for item in reports
                        ],
                        "disagreements": ranked_disagreements,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return
        typer.echo("judge judgments labeled kappa position_bias length_corr self_pref status")
        for item in reports:
            value = item.calibration
            typer.echo(
                f"{value.judge_id} {value.judgments} {value.human_labeled} "
                f"{_fmt(value.cohens_kappa)} {_fmt(value.position_bias_rate)} "
                f"{_fmt(value.length_correlation)} {_fmt(value.self_preference_rate)} {item.status}"
            )
        if disagreement_limit:
            typer.echo("\nLargest judge/human disagreements")
            for row in ranked_disagreements:
                typer.echo(
                    f"{row['judge_id']} {row['item_id']}: judge={row['judge_verdict']} "
                    f"human={row['human_verdict']} severity={row['severity']}"
                )
    except (GoldSetError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error


judges_app.command(name="report")(report)
app.add_typer(judges_app)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"

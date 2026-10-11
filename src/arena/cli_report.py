"""Markdown report and static bundle export commands."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from arena.cli import app
from arena.core.bundle import (
    BundleError,
    compute_stats,
    default_home,
    export_bundle,
    open_run,
    render_report,
)
from arena.server.static import ViewerAssetsUnavailable, export_viewer_assets

app.registered_callback  # noqa: B018 - importing this module registers its commands


@app.command()
def report(
    run_id: Annotated[str, typer.Argument(help="Run ID to summarize.")],
    format: Annotated[str, typer.Option("--format", help="Report format (md).")] = "md",
    home: Annotated[Path | None, typer.Option("--home", help="Arena data directory.")] = None,
    against: Annotated[
        str | None, typer.Option("--against", help="Baseline run ID for a diff.")
    ] = None,
) -> None:
    """Print a Markdown leaderboard and run summary."""
    if format != "md":
        typer.echo(f"unsupported report format: {format}", err=True)
        raise typer.Exit(code=1)
    data_home = home or default_home()
    try:
        records = open_run(data_home, run_id)
        baseline = open_run(data_home, against) if against else None
        stats = compute_stats(records, baseline=baseline)
        typer.echo(render_report(records, stats), nl=False)
    except BundleError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error


@app.command()
def export(
    run_id: Annotated[str, typer.Argument(help="Run ID to export.")],
    out: Annotated[Path, typer.Option("--out", help="New output directory.")],
    format: Annotated[
        str, typer.Option("--format", help="Export format (bundle or static).")
    ] = "bundle",
    home: Annotated[Path | None, typer.Option("--home", help="Arena data directory.")] = None,
) -> None:
    """Export a validated static report bundle and its referenced blobs."""
    if format not in {"bundle", "static"}:
        typer.echo(f"unsupported export format: {format}", err=True)
        raise typer.Exit(code=1)
    data_home = home or default_home()
    try:
        records = open_run(data_home, run_id)
        stats = compute_stats(records)
        if format == "bundle":
            export_bundle(records, stats, out, data_home)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
            shutil.rmtree(staging)
            try:
                export_bundle(records, stats, staging, data_home)
                export_viewer_assets(
                    staging,
                    bundle=(staging / "bundle.json").read_bytes(),
                    events=(staging / "events.jsonl").read_bytes(),
                )
                staging.rename(out)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
    except (BundleError, ViewerAssetsUnavailable) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Exported run {run_id} to {out}")

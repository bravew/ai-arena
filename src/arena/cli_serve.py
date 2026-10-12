"""Serve the local or remote Arena viewer and API."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from arena.cli import app
from arena.server.app import serve


@app.command("serve")
def serve_command(
    host: Annotated[str, typer.Option("--host", help="Interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Viewer and API port.")] = 7400,
    artifact_port: Annotated[
        int, typer.Option("--artifact-port", help="HTML artifact origin port.")
    ] = 7402,
    home: Annotated[Path | None, typer.Option("--home", help="Arena data directory.")] = None,
    providers: Annotated[Path, typer.Option("--providers")] = Path("providers.yaml"),
    catalog: Annotated[Path, typer.Option("--catalog")] = Path("catalog/models.yaml"),
    project_root: Annotated[
        Path | None,
        typer.Option(
            "--project-root",
            help="Directory holding suites/ and contestants/ for runs started from the viewer.",
        ),
    ] = None,
    run_key: Annotated[
        str | None,
        typer.Option(
            "--run-key", envvar="ARENA_RUN_KEY", help="Run key required for off-box binding."
        ),
    ] = None,
    trusted_proxy: Annotated[
        str | None,
        typer.Option(
            "--trusted-proxy",
            envvar="ARENA_TRUSTED_PROXY",
            help="Trusted proxy IP that terminates TLS for off-box access.",
        ),
    ] = None,
) -> None:
    """Serve the viewer API locally or with run-key protection remotely."""
    try:
        serve(
            host,
            port,
            artifact_port,
            home,
            run_key or os.environ.get("ARENA_RUN_KEY"),
            trusted_proxy or os.environ.get("ARENA_TRUSTED_PROXY"),
            provider_config=providers,
            provider_catalog=catalog,
            project_root=project_root,
        )
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

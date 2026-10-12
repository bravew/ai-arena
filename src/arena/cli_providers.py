"""Output for the vendor-CLI checks behind `providers detect`, `providers test` and `doctor`."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

import typer

from arena.providers.config import Provider, load_providers
from arena.providers.models_probe import (
    DiscoveryError,
    append_subscription_entries,
    discover_models,
    propose,
    render,
)
from arena.providers.runtimes import (
    RuntimeReport,
    RuntimeStatus,
    detect_all,
    detect_runtime,
    spec_for_vendor,
)


def format_report(report: RuntimeReport) -> list[str]:
    version = f" {report.version}" if report.version else ""
    lines = [f"{report.runtime}: {report.status}{version} ({report.detail})"]
    if report.hint:
        lines.append(f"  next: {report.hint}")
    return lines


def echo_detect(*, json_output: bool, environ: Mapping[str, str] | None = None) -> None:
    """Print every vendor CLI's state. Absent CLIs are reported, never an error."""
    reports = detect_all(environ)
    if json_output:
        typer.echo(json.dumps([report.to_dict() for report in reports], indent=2))
        return
    for report in reports:
        for line in format_report(report):
            typer.echo(line)


def doctor_lines(environ: Mapping[str, str] | None = None) -> list[str]:
    """Vendor CLI lines for `arena doctor`. A missing CLI is optional, so it never fails."""
    return [
        f"CLI {line}" if not line.startswith("  ") else line
        for report in detect_all(environ)
        for line in format_report(report)
    ]


def check_local_cli_provider(
    provider: Provider, environ: Mapping[str, str] | None = None
) -> tuple[bool, list[str]]:
    """Typed result for a `local-cli` subscription provider, and whether it passed.

    This runs the same status commands as `detect` and sends no model request. The one
    capped call that proves a model answers needs the local-cli runner and a maintainer's
    approval to spend quota, so it is not made here.
    """
    subscription = provider.subscription
    spec = spec_for_vendor(subscription.vendor) if subscription is not None else None
    if spec is None:
        return False, [f"{provider.id}: no CLI contract for this provider"]
    report = detect_runtime(spec, environ)
    lines = [
        f"{provider.id}: {line}" if i == 0 else line for i, line in enumerate(format_report(report))
    ]
    return report.status is RuntimeStatus.OK, lines


def models_command(
    provider_id: Annotated[str, typer.Argument(metavar="PROVIDER", help="Provider id.")],
    config: Annotated[Path, typer.Option("--config", exists=True, dir_okay=False)] = Path(
        "providers.yaml"
    ),
    catalog: Annotated[Path, typer.Option("--catalog", dir_okay=False)] = Path(
        "catalog/models.yaml"
    ),
    write: Annotated[
        bool, typer.Option("--write", help="Append subscription entries to the catalog.")
    ] = False,
    home: Annotated[Path | None, typer.Option("--home")] = None,
) -> None:
    """List a provider's models and propose catalog entries. Sends no model request."""
    try:
        providers = load_providers(config)
    except (OSError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    provider = next((item for item in providers.providers if item.id == provider_id), None)
    if provider is None:
        typer.echo(f"error: no provider {provider_id!r} in {config}", err=True)
        raise typer.Exit(code=1)
    try:
        models = discover_models(provider, home=home)
        proposals = propose(provider, models, catalog)
    except (DiscoveryError, OSError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    new = {item.ref for item in proposals}
    typer.echo(f"{provider.id}: {len(models)} model(s)")
    for model in models:
        flag = "new" if f"{provider.id}/{model.id}" in new else "in catalog"
        typer.echo(f"  {model.id}  [{flag}]")
    if not proposals:
        return
    typer.echo("\nProposed catalog entries:")
    typer.echo(render(proposals), nl=False)
    paid = [item for item in proposals if not item.subscription]
    if paid:
        typer.echo(
            f"{len(paid)} paid model(s) have no price. Add a verified price_per_mtok and its "
            "source by hand; metered runs refuse a model with no price."
        )
    if write:
        try:
            written = append_subscription_entries(catalog, proposals)
        except (OSError, ValueError) as error:
            typer.echo(f"error: catalog not changed: {error}", err=True)
            raise typer.Exit(code=1) from error
        typer.echo(f"Wrote {len(written)} subscription entries to {catalog}")

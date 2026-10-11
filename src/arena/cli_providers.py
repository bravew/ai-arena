"""Output for the vendor-CLI checks behind `providers detect`, `providers test` and `doctor`."""

from __future__ import annotations

import json
from collections.abc import Mapping

import typer

from arena.providers.config import Provider
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

"""The `arena` command line. Subcommands are added by their own modules."""

from importlib import import_module
from pathlib import Path
from typing import Annotated

import typer
import yaml

from arena import __version__
from arena.cli_selfcheck import selfcheck
from arena.cli_score import create_score_command
from arena.core.config import validate_document
from arena.providers.config import parse_yaml, reject_plaintext_secrets
from arena.scorers.registry import default_registry

app = typer.Typer(
    name="arena",
    help="Compare AI models, agents, and kits on coding and content tasks.",
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"arena {__version__}")
        raise typer.Exit()


@app.command()
def validate(
    paths: Annotated[list[Path], typer.Argument(help="Config files or directories to validate.")],
) -> None:
    """Validate provider, catalog, suite, contestant, kit, and rubric YAML files."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.yaml")))
            files.extend(sorted(path.rglob("*.yml")))
        else:
            files.append(path)

    issues: list[str] = []
    for path in files:
        try:
            if not path.is_file():
                issues.append(f"{path}:<root>: file does not exist")
                continue
            raw = parse_yaml(path)
            issues.extend(reject_plaintext_secrets(raw, path))
            issues.extend(validate_document(path, raw))
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as error:
            issues.append(f"{path}:<root>: {error}")

    if issues:
        for issue in issues:
            typer.echo(issue, err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Validated {len(files)} config file(s).")


@app.command(name="selfcheck")
def selfcheck_command(
    paths: Annotated[list[Path], typer.Argument(help="Suite directories or task.yaml files.")],
    trust_task_code: Annotated[
        bool,
        typer.Option(
            "--trust-task-code",
            help="Confirm these local suites and scorer commands are trusted to run on this host.",
        ),
    ] = False,
) -> None:
    """Run trusted task scorer commands with host privileges against oracle and null."""
    selfcheck(paths, trust_task_code=trust_task_code)
app.command("score")(create_score_command(default_registry()))


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Print the version and exit.",
        ),
    ] = False,
) -> None:
    """Compare AI models, agents, and kits on coding and content tasks."""


import_module("arena.cli_report")

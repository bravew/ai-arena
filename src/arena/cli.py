"""The `arena` command line. Subcommands are added by their own modules."""

from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from arena import __version__
from arena.catalog.config import ModelCatalog
from arena.providers.config import (
    ProviderConfig,
    format_validation_error,
    parse_yaml,
    reject_plaintext_secrets,
)

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
    """Validate provider and model catalog YAML files."""
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
            try:
                if path.name == "providers.yaml":
                    ProviderConfig.model_validate(raw)
                elif path.name == "models.yaml":
                    ModelCatalog.model_validate(raw)
                else:
                    issues.append(
                        f"{path}:<root>: unsupported config file "
                        "(expected providers.yaml or models.yaml)"
                    )
            except ValidationError as error:
                issues.extend(format_validation_error(path, error))
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as error:
            issues.append(f"{path}:<root>: {error}")

    if issues:
        for issue in issues:
            typer.echo(issue, err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Validated {len(files)} config file(s).")


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

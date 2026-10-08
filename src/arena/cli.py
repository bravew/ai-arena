"""The `arena` command line. Subcommands are added by their own modules."""

from typing import Annotated

import typer

from arena import __version__

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

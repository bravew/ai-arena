from typer.testing import CliRunner

from arena import __version__
from arena.cli import app


def test_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"arena {__version__}"


def test_no_arguments_prints_help() -> None:
    result = CliRunner().invoke(app, [])
    assert "Compare AI models" in result.output

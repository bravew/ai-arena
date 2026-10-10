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


def test_serve_command_is_registered() -> None:
    result = CliRunner().invoke(app, ["serve", "--help"], terminal_width=120)
    assert result.exit_code == 0
    assert "--artifact-port" in result.output


def test_serve_refuses_off_box_binding_without_key() -> None:
    result = CliRunner().invoke(app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code == 2
    assert "requires ARENA_RUN_KEY" in result.output


def test_export_supports_static_format() -> None:
    result = CliRunner().invoke(app, ["export", "--help"])
    assert result.exit_code == 0
    assert "static" in result.output

"""Fake `claude` and `codex` executables that replay fixtures/cli-contracts."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).parents[2] / "fixtures" / "cli-contracts"


def write_script(path: Path, body: str, mode: int = 0o755) -> Path:
    # The probe's PATH holds only the fake's directory, so the script finds its own tools.
    path.write_text("#!/bin/sh\nPATH=/usr/bin:/bin\n" + body, encoding="utf-8")
    path.chmod(mode)
    return path


def fake_claude(directory: Path, *, version: str | None = None, auth: str | None = None) -> Path:
    """Signed in by an inherited token or `$HOME/.signed-in`, as the real CLI behaves."""
    claude = FIXTURES / "claude"
    version_text = version or (claude / "version.txt").read_text(encoding="utf-8")
    signed_in = auth or str(claude / "auth-status.signed-in.json")
    signed_out = auth or str(claude / "auth-status.signed-out.json")
    return write_script(
        directory / "claude",
        f"""case "$1" in
--version) printf '%s\\n' '{version_text.strip()}' ;;
auth)
  if [ -n "$ANTHROPIC_AUTH_TOKEN" ] || [ -n "$ANTHROPIC_API_KEY" ] \\
    || [ -f "$HOME/.signed-in" ]; then
    cat '{signed_in}'
  else
    cat '{signed_out}'
  fi ;;
esac
""",
    )


def fake_codex(directory: Path, *, version: str | None = None, models: str | None = None) -> Path:
    codex = FIXTURES / "codex"
    version_text = version or (codex / "version.txt").read_text(encoding="utf-8")
    models_file = models or str(codex / "debug-models.empty-home.json")
    return write_script(
        directory / "codex",
        f"""case "$1 $2" in
"--version ") printf '%s\\n' '{version_text.strip()}' ;;
"login status")
  if [ -f "$CODEX_HOME/auth.json" ]; then cat '{codex / "login-status.signed-in.txt"}'
  else cat '{codex / "login-status.signed-out.txt"}'; exit 1; fi ;;
"debug models")
  printf '%s' "$CODEX_HOME" > "$HOME/codex-home-seen"
  cat '{models_file}' ;;
esac
""",
    )

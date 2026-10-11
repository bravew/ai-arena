"""Runtime detection against fake `claude` and `codex` executables.

The fakes replay the recorded outputs under fixtures/cli-contracts, so a change in the
contract shows up as a failing fixture, not a hand-edited string.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.cli_providers import doctor_lines
from arena.providers.config import load_providers
from arena.providers.runtimes import (
    CLAUDE,
    CODEX,
    AuthState,
    RuntimeStatus,
    check_model,
    detect_all,
    detect_runtime,
    probe_environment,
)
from tests.providers.fakes import fake_claude, fake_codex, write_script


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


def _environ(home: Path, **extra: str) -> dict[str, str]:
    return {"PATH": str(home / "bin"), "HOME": str(home), **extra}


@pytest.fixture
def bin_dir(home: Path) -> Path:
    path = home / "bin"
    path.mkdir()
    return path


def test_claude_signed_in_is_ok(home: Path, bin_dir: Path) -> None:
    fake_claude(bin_dir)
    (home / ".signed-in").touch()
    report = detect_runtime(CLAUDE, _environ(home))
    assert (report.status, report.auth, report.version) == (
        RuntimeStatus.OK,
        AuthState.AUTHENTICATED,
        "2.1.296",
    )
    assert report.binary == str(bin_dir / "claude")


def test_claude_signed_out_needs_auth_and_names_the_login_command(
    home: Path, bin_dir: Path
) -> None:
    fake_claude(bin_dir)
    report = detect_runtime(CLAUDE, _environ(home))
    assert report.status is RuntimeStatus.AUTH_REQUIRED
    assert report.auth is AuthState.UNAUTHENTICATED
    assert report.hint is not None
    assert "claude auth login" in report.hint


def test_claude_probe_ignores_a_token_in_the_callers_environment(home: Path, bin_dir: Path) -> None:
    fake_claude(bin_dir)
    environ = _environ(
        home, ANTHROPIC_AUTH_TOKEN="x", ANTHROPIC_API_KEY="y", ANTHROPIC_BASE_URL="z"
    )
    report = detect_runtime(CLAUDE, environ)
    assert report.status is RuntimeStatus.AUTH_REQUIRED


def test_probe_environment_forwards_only_the_allowlist_and_no_empty_values() -> None:
    env = probe_environment(
        {
            "PATH": "/bin",
            "HOME": "/h",
            "HTTPS_PROXY": "http://p",
            "ANTHROPIC_API_KEY": "k",
            "OPENAI_API_KEY": "k",
            "CODEX_API_KEY": "k",
            "TMPDIR": "",
        }
    )
    assert env == {"PATH": "/bin", "HOME": "/h", "HTTPS_PROXY": "http://p"}


def test_codex_signed_in_and_out(home: Path, bin_dir: Path, tmp_path: Path) -> None:
    fake_codex(bin_dir)
    out = detect_runtime(CODEX, _environ(home, CODEX_HOME=str(tmp_path / "empty")))
    assert (out.status, out.auth) == (RuntimeStatus.AUTH_REQUIRED, AuthState.UNAUTHENTICATED)
    signed = tmp_path / "signed"
    signed.mkdir()
    (signed / "auth.json").write_text("{}", encoding="utf-8")
    ok = detect_runtime(CODEX, _environ(home, CODEX_HOME=str(signed)))
    assert (ok.status, ok.auth, ok.version) == (
        RuntimeStatus.OK,
        AuthState.AUTHENTICATED,
        "0.162.1",
    )
    assert ok.detail == "Logged in using ChatGPT"


def test_not_installed_has_an_install_hint(home: Path) -> None:
    report = detect_runtime(CLAUDE, _environ(home), find=lambda spec, environ: None)
    assert report.status is RuntimeStatus.NOT_INSTALLED
    assert report.auth is AuthState.UNKNOWN
    assert report.hint is not None
    assert "npm install" in report.hint


def test_override_variable_to_a_missing_file_is_not_installed(home: Path) -> None:
    environ = _environ(home, CLAUDE_CODE_PATH=str(home / "nope"))
    assert detect_runtime(CLAUDE, environ).status is RuntimeStatus.NOT_INSTALLED


def test_override_variable_wins_over_path(home: Path, bin_dir: Path, tmp_path: Path) -> None:
    fake_claude(bin_dir)
    other = tmp_path / "other"
    other.mkdir()
    chosen = fake_claude(other, version="2.9.0 (Claude Code)")
    (home / ".signed-in").touch()
    report = detect_runtime(CLAUDE, _environ(home, CLAUDE_CODE_PATH=str(chosen)))
    assert (report.binary, report.version) == (str(chosen), "2.9.0")


def test_a_binary_in_a_common_install_dir_is_found(home: Path) -> None:
    local = home / ".local" / "bin"
    local.mkdir(parents=True)
    fake_claude(local)
    (home / ".signed-in").touch()
    report = detect_runtime(CLAUDE, {"PATH": "", "HOME": str(home)})
    assert report.status is RuntimeStatus.OK
    assert report.binary == str(local / "claude")


def test_old_version_is_incompatible_and_names_the_recorded_one(home: Path, bin_dir: Path) -> None:
    fake_claude(bin_dir, version="1.0.3 (Claude Code)")
    report = detect_runtime(CLAUDE, _environ(home))
    assert report.status is RuntimeStatus.INCOMPATIBLE_VERSION
    assert "2.1.296" in report.detail


def test_version_without_a_number_is_a_protocol_failure(home: Path, bin_dir: Path) -> None:
    fake_claude(bin_dir, version="hello")
    assert detect_runtime(CLAUDE, _environ(home)).status is RuntimeStatus.PROTOCOL_FAILURE


def test_auth_output_that_is_not_json_is_a_protocol_failure(
    home: Path, bin_dir: Path, tmp_path: Path
) -> None:
    garbage = tmp_path / "garbage.txt"
    garbage.write_text("not json", encoding="utf-8")
    fake_claude(bin_dir, auth=str(garbage))
    report = detect_runtime(CLAUDE, _environ(home))
    assert report.status is RuntimeStatus.PROTOCOL_FAILURE
    assert report.auth is AuthState.UNKNOWN


def test_a_binary_that_cannot_be_executed_is_a_permission_failure(
    home: Path, tmp_path: Path
) -> None:
    binary = write_script(tmp_path / "claude", "exit 0\n", mode=stat.S_IRUSR | stat.S_IWUSR)
    report = detect_runtime(CLAUDE, _environ(home, CLAUDE_CODE_PATH=str(binary)))
    assert report.status is RuntimeStatus.PERMISSION_FAILURE


def test_check_model_reads_the_list_under_an_isolated_codex_home(
    home: Path, bin_dir: Path, tmp_path: Path
) -> None:
    fake_codex(bin_dir)
    user_home = tmp_path / "user-codex"
    user_home.mkdir()
    (user_home / "auth.json").write_text("{}", encoding="utf-8")
    environ = _environ(home, CODEX_HOME=str(user_home))
    report = detect_runtime(CODEX, environ)
    assert report.status is RuntimeStatus.OK

    assert check_model(report, "gpt-6.1-sol", environ).status is RuntimeStatus.OK
    seen = (home / "codex-home-seen").read_text(encoding="utf-8")
    assert seen and seen != str(user_home)
    assert not os.path.exists(seen)

    missing = check_model(report, "group/coder", environ)
    assert missing.status is RuntimeStatus.UNSUPPORTED_MODEL
    assert "gpt-6.1-sol" in missing.detail
    # Hidden models are not offered.
    assert (
        check_model(report, "codex-auto-review", environ).status is RuntimeStatus.UNSUPPORTED_MODEL
    )


def test_check_model_passes_a_cli_with_no_list_command_and_skips_a_failed_runtime(
    home: Path, bin_dir: Path
) -> None:
    fake_claude(bin_dir)
    (home / ".signed-in").touch()
    environ = _environ(home)
    report = detect_runtime(CLAUDE, environ)
    assert check_model(report, "claude-opus-5-5", environ) is report
    failed = detect_runtime(CODEX, environ, find=lambda spec, env: None)
    assert check_model(failed, "gpt-6.1-sol", environ) is failed


def test_detect_all_reports_every_cli_with_none_installed(home: Path) -> None:
    reports = detect_all(_environ(home), find=lambda spec, environ: None)
    assert [report.status for report in reports] == [RuntimeStatus.NOT_INSTALLED] * 2


def test_report_json_carries_no_secret_field_names(home: Path, bin_dir: Path) -> None:
    fake_claude(bin_dir)
    payload = json.dumps(detect_runtime(CLAUDE, _environ(home)).to_dict())
    assert "token" not in payload.lower()


def test_providers_detect_json_with_none_installed_exits_zero(home: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["providers", "detect", "--json"],
        env={
            "HOME": str(home),
            "PATH": "",
            "SHELL": "",
            "CLAUDE_CODE_PATH": str(home / "missing-claude"),
            "CODEX_CLI_PATH": str(home / "missing-codex"),
        },
    )
    assert result.exit_code == 0, result.output
    assert [item["status"] for item in json.loads(result.output)] == ["not_installed"] * 2


def test_providers_detect_text_shows_version_and_next_step(home: Path, bin_dir: Path) -> None:
    claude = fake_claude(bin_dir)
    codex = fake_codex(bin_dir)
    result = CliRunner().invoke(
        app,
        ["providers", "detect"],
        env={
            "HOME": str(home),
            "PATH": str(bin_dir),
            "CLAUDE_CODE_PATH": str(claude),
            "CODEX_CLI_PATH": str(codex),
            "CODEX_HOME": str(home / "no-codex"),
        },
    )
    assert result.exit_code == 0, result.output
    assert "claude: auth_required 2.1.296" in result.output
    assert "next: Sign in: claude auth login" in result.output
    assert "codex: auth_required 0.162.1" in result.output


def test_providers_test_checks_a_local_cli_provider_without_a_model_call(
    home: Path, bin_dir: Path, tmp_path: Path
) -> None:
    codex = fake_codex(bin_dir)
    signed = tmp_path / "codex-home"
    signed.mkdir()
    (signed / "auth.json").write_text("{}", encoding="utf-8")
    config = tmp_path / "providers.yaml"
    config.write_text(
        "providers:\n  - id: openai-plus\n    keys: []\n    subscription:\n"
        "      vendor: chatgpt\n      plan: plus\n      import: own-sign-in\n"
        "      runtime: local-cli\n",
        encoding="utf-8",
    )
    assert load_providers(config).providers[0].subscription is not None
    env = {
        "HOME": str(home),
        "PATH": str(bin_dir),
        "CODEX_CLI_PATH": str(codex),
        "CODEX_HOME": str(signed),
    }
    ok = CliRunner().invoke(app, ["providers", "test", "--config", str(config)], env=env)
    assert ok.exit_code == 0, ok.output
    assert "openai-plus: codex: ok 0.162.1" in ok.output

    env["CODEX_HOME"] = str(tmp_path / "signed-out")
    out = CliRunner().invoke(app, ["providers", "test", "--config", str(config)], env=env)
    assert out.exit_code == 1
    assert "next: Sign in: codex login" in out.output


def test_doctor_lines_list_the_clis_and_never_fail(home: Path) -> None:
    lines = doctor_lines(
        {
            "HOME": str(home),
            "PATH": "",
            "SHELL": "",
            "CLAUDE_CODE_PATH": str(home / "missing-claude"),
            "CODEX_CLI_PATH": str(home / "missing-codex"),
        }
    )
    assert any(line.startswith("CLI claude: not_installed") for line in lines)
    assert any(line.startswith("CLI codex: not_installed") for line in lines)

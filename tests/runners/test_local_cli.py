"""The local-cli runner against fake `claude` and `codex` executables.

The stream fixtures are marked `synthetic`: they follow Neumar's handling of the two CLIs
and are replaced by captured output in the live check (#194).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shlex
from pathlib import Path

import pytest

from arena.agents.local import (
    ClaudeLocalAdapter,
    CodexLocalAdapter,
    SignIn,
    SignInError,
    claude_sign_in,
    codex_sign_in,
)
from arena.runners.local_cli import (
    FailureKind,
    LocalCliRequest,
    LocalCliResult,
    _collect_outputs,  # pyright: ignore[reportPrivateUsage]
    adapter_for_runtime,
    run_local_cli,
    served_matches,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "cli-contracts"
CLAUDE_STREAM = FIXTURES / "claude" / "stream.synthetic.jsonl"
CODEX_STREAM = FIXTURES / "codex" / "exec.synthetic.jsonl"
SECRET = b"SIGN-IN-SECRET-VALUE"


def _fake(
    directory: Path,
    name: str,
    stream: Path | str,
    record: Path,
    *,
    exit_code: int = 0,
    sleep: float = 0,
    answer: bool = True,
    rollout_model: str | None = None,
) -> Path:
    """A fake CLI. It records what it was given in `record`, then replays `stream`."""
    stream_cat = (
        f"cat {shlex.quote(str(stream))}"
        if isinstance(stream, Path)
        else f"printf '%s\\n' {shlex.quote(stream)}"
    )
    rollout = ":"
    if rollout_model:
        line = f'{{"type":"turn_context","payload":{{"model":"{rollout_model}"}}}}'
        rollout = (
            'mkdir -p "$CODEX_HOME/sessions/2026" && '
            f"echo '{line}' > \"$CODEX_HOME/sessions/2026/rollout-1.jsonl\""
        )
    script = directory / name
    script.write_text(
        f"""#!/bin/sh
PATH=/usr/bin:/bin
if [ "$1" = "--version" ]; then printf '%s\\n' '2.1.296 (Claude Code)'; exit 0; fi
{{
  echo "pid=$$"
  echo "argv=$*"
  echo "home=$HOME"
  echo "cwd=$(pwd)"
  echo "claude_config_dir=$CLAUDE_CONFIG_DIR"
  echo "codex_home=$CODEX_HOME"
  env | cut -d= -f1 | sort | sed 's/^/envname=/'
  for f in "$HOME/.claude/.credentials.json" "$HOME/.codex/auth.json"; do
    if [ -f "$f" ]; then echo "signin=$(ls -l "$f" | cut -c1-10) $(wc -c < "$f" | tr -d ' ')"; fi
  done
  [ -f task-input/prompt.md ] && echo "task-input=present"
  [ -f greeting.py ] && echo "fixture=$(cat greeting.py)"
}} > {shlex.quote(str(record))}
sleep {sleep}
{"echo done > answer.md" if answer else ":"}
{rollout}
{stream_cat}
exit {exit_code}
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _versioned_claude(
    directory: Path, stream: Path, record: Path, version: str, *, exit_code: int = 0
) -> Path:
    """A fake `claude` that answers `--version`, then behaves like `_fake`."""
    script = directory / "claude"
    script.write_text(
        f"""#!/bin/sh
PATH=/usr/bin:/bin
if [ "$1" = "--version" ]; then printf '%s\\n' {shlex.quote(version)}; exit {exit_code}; fi
echo "argv=$*" > {shlex.quote(str(record))}
echo done > answer.md
cat {shlex.quote(str(stream))}
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _environ(home: Path, **extra: str) -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "HOME": str(home), **extra}


def _request(
    binary: Path,
    scratch: Path,
    *,
    runtime: str = "claude",
    model: str = "claude-opus-5-5",
    home: Path | None = None,
    timeout: float = 20.0,
    **extra: str,
) -> LocalCliRequest:
    return LocalCliRequest(
        adapter=adapter_for_runtime(runtime),
        binary=str(binary),
        model=model,
        prompt="Write answer.md",
        sign_in=SignIn({".claude/.credentials.json": SECRET}),
        timeout=timeout,
        environ=_environ(home or scratch.parent / "real-home", **extra),
        scratch_base=scratch,
        task_files={"prompt.md": b"task"},
    )


def _record(path: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        result.setdefault(key, []).append(value)
    return result


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    binary_dir, scratch, home = tmp_path / "bin", tmp_path / "scratch", tmp_path / "real-home"
    for path in (binary_dir, scratch, home):
        path.mkdir()
    return binary_dir, scratch, home


def _tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_claude_success_reports_model_usage_and_outputs(dirs: tuple[Path, Path, Path]) -> None:
    binary_dir, scratch, home = dirs
    record = binary_dir / "record"
    claude = _fake(binary_dir, "claude", CLAUDE_STREAM, record)
    result = asyncio.run(run_local_cli(_request(claude, scratch, home=home)))
    assert result.ok, result.detail
    assert result.summary is not None
    assert result.summary.model_served == "claude-opus-5-5"
    assert result.summary.usage is not None
    assert (result.summary.usage.input_tokens, result.summary.usage.output_tokens) == (12, 5)
    assert result.summary.usage.cache_read_tokens == 3
    assert result.model_served_verified
    assert result.outputs == {"answer.md": b"done\n"}
    assert list(scratch.iterdir()) == []


def test_child_gets_scratch_home_allowlisted_env_and_narrow_flags(
    dirs: tuple[Path, Path, Path],
) -> None:
    binary_dir, scratch, home = dirs
    record = binary_dir / "record"
    claude = _fake(binary_dir, "claude", CLAUDE_STREAM, record)
    request = _request(
        claude,
        scratch,
        home=home,
        ANTHROPIC_API_KEY="k",
        ANTHROPIC_AUTH_TOKEN="t",
        OPENAI_API_KEY="o",
        CODEX_API_KEY="c",
    )
    assert asyncio.run(run_local_cli(request)).ok
    seen = _record(record)
    names = set(seen["envname"])
    assert not names & {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
    }
    assert seen["home"][0].startswith(str(scratch)) and seen["home"][0] != str(home)
    assert seen["claude_config_dir"][0] == seen["home"][0] + "/.claude"
    assert seen["cwd"][0].startswith(str(scratch))
    assert seen["signin"] == [f"-rw------- {len(SECRET)}"]
    assert seen["task-input"] == ["present"]
    argv = seen["argv"][0]
    assert "--model claude-opus-5-5" in argv
    assert "--allowedTools Read Write Edit Glob Grep" in argv
    assert "--tools Read,Write,Edit,Glob,Grep" in argv
    assert "--restricted" in argv
    assert "--safe-mode" in argv
    assert "--strict-mcp-config" in argv
    assert "--permission-prompts none" in argv
    assert "--disallowedTools mcp__*" in argv
    assert "Bash" not in argv
    assert str(request.environ["HOME"]) not in argv


@pytest.mark.parametrize(
    ("version", "exit_code"),
    [("2.1.247 (Claude Code)", 0), ("1.0.3 (Claude Code)", 0), ("no version here", 0), ("", 3)],
)
def test_a_claude_older_than_the_restricted_mode_floor_fails_before_any_work(
    dirs: tuple[Path, Path, Path], version: str, exit_code: int
) -> None:
    binary_dir, scratch, home = dirs
    record = binary_dir / "record"
    claude = _versioned_claude(binary_dir, CLAUDE_STREAM, record, version, exit_code=exit_code)
    result = asyncio.run(run_local_cli(_request(claude, scratch, home=home)))
    assert result.failure is FailureKind.PROTOCOL
    assert "2.1.248" in result.detail
    # The check runs before the sign-in is staged, so no trial process ever starts.
    assert not record.exists()
    assert result.summary is None
    assert result.outputs == {}
    assert list(scratch.iterdir()) == []


def test_a_supported_claude_version_reaches_the_trial(dirs: tuple[Path, Path, Path]) -> None:
    binary_dir, scratch, home = dirs
    record = binary_dir / "record"
    claude = _versioned_claude(binary_dir, CLAUDE_STREAM, record, "2.1.248 (Claude Code)")
    result = asyncio.run(run_local_cli(_request(claude, scratch, home=home)))
    assert result.ok, result.detail
    assert record.exists()


def test_codex_success_reads_the_served_model_from_the_session_record(
    dirs: tuple[Path, Path, Path],
) -> None:
    binary_dir, scratch, home = dirs
    record = binary_dir / "record"
    codex = _fake(binary_dir, "codex", CODEX_STREAM, record, rollout_model="gpt-6.1-sol")
    request = _request(codex, scratch, runtime="codex", model="gpt-6.1-sol", home=home)
    result = asyncio.run(run_local_cli(request))
    assert result.ok, result.detail
    assert result.summary is not None and result.summary.usage is not None
    usage = result.summary.usage
    # Cached input is reported apart from fresh input.
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (60, 40, 9)
    assert usage.reasoning_tokens == 4
    assert result.model_served_verified
    seen = _record(record)
    assert "--sandbox workspace-write" in seen["argv"][0]
    assert seen["codex_home"][0] == seen["home"][0] + "/.codex"
    assert list(scratch.iterdir()) == []


def test_codex_without_a_session_record_succeeds_but_is_marked_unverified(
    dirs: tuple[Path, Path, Path],
) -> None:
    binary_dir, scratch, home = dirs
    codex = _fake(binary_dir, "codex", CODEX_STREAM, binary_dir / "record")
    result = asyncio.run(
        run_local_cli(_request(codex, scratch, runtime="codex", model="gpt-6.1-sol", home=home))
    )
    assert result.ok
    assert not result.model_served_verified


def _failure(
    dirs: tuple[Path, Path, Path], stream: Path | str, **fake_options: object
) -> LocalCliResult:
    binary_dir, scratch, home = dirs
    claude = _fake(binary_dir, "claude", stream, binary_dir / "record", **fake_options)  # type: ignore[arg-type]
    return asyncio.run(run_local_cli(_request(claude, scratch, home=home, timeout=3.0)))


def test_a_different_model_is_a_failure_never_a_fallback(dirs: tuple[Path, Path, Path]) -> None:
    other = (
        '{"type":"system","subtype":"init","model":"claude-sonnet-4-5"}\n'
        '{"type":"result","is_error":false,"result":"x","usage":{"input_tokens":1,"output_tokens":1}}'
    )
    result = _failure(dirs, other)
    assert result.failure is FailureKind.MODEL_MISMATCH
    assert "claude-sonnet-4-5" in result.detail


def test_a_dated_alias_of_the_requested_model_matches() -> None:
    assert served_matches("claude-sonnet-4-5", "claude-sonnet-4-5-20250929")
    assert not served_matches("claude-sonnet-4-5", "claude-sonnet-4-5-latest")
    assert not served_matches("claude-opus-5-5", "claude-opus-5-5-sub")


def test_limit_and_auth_failures_are_typed_even_with_a_zero_exit(
    dirs: tuple[Path, Path, Path],
) -> None:
    limit = (
        '{"type":"system","subtype":"init","model":"claude-opus-5-5"}\n'
        '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected"}}\n'
        '{"type":"result","is_error":true,"result":"Claude usage limit reached"}'
    )
    assert _failure(dirs, limit).failure is FailureKind.LIMIT_REACHED
    auth = (
        '{"type":"system","subtype":"init","model":"claude-opus-5-5"}\n'
        '{"type":"result","is_error":true,"result":"Invalid API key · Please run /login"}'
    )
    assert _failure(dirs, auth).failure is FailureKind.AUTH_REQUIRED


def test_missing_usage_is_not_a_success(dirs: tuple[Path, Path, Path]) -> None:
    stream = (
        '{"type":"system","subtype":"init","model":"claude-opus-5-5"}\n'
        '{"type":"result","is_error":false,"result":"x"}'
    )
    assert _failure(dirs, stream).failure is FailureKind.NO_USAGE


def test_nonzero_exit_and_garbage_output_are_typed(dirs: tuple[Path, Path, Path]) -> None:
    assert _failure(dirs, CLAUDE_STREAM, exit_code=3).failure is FailureKind.EXIT_CODE
    garbage = _failure(dirs, "not json at all")
    assert garbage.failure is FailureKind.PROTOCOL


def test_timeout_kills_the_cli(dirs: tuple[Path, Path, Path]) -> None:
    binary_dir, scratch, home = dirs
    record = binary_dir / "record"
    claude = _fake(binary_dir, "claude", CLAUDE_STREAM, record, sleep=30)
    request = _request(claude, scratch, home=home, timeout=1.0)
    result = asyncio.run(run_local_cli(request))
    assert result.failure is FailureKind.TIMEOUT
    pid = int(_record(record)["pid"][0])
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_spawn_failure_is_typed(dirs: tuple[Path, Path, Path]) -> None:
    _, scratch, home = dirs
    result = asyncio.run(run_local_cli(_request(Path("/nonexistent/claude"), scratch, home=home)))
    assert result.failure is FailureKind.SPAWN


def test_cancel_stops_the_cli_and_removes_the_scratch_dir(dirs: tuple[Path, Path, Path]) -> None:
    binary_dir, scratch, home = dirs
    record = binary_dir / "record"
    claude = _fake(binary_dir, "claude", CLAUDE_STREAM, record, sleep=30)

    async def scenario() -> None:
        task = asyncio.create_task(run_local_cli(_request(claude, scratch, home=home)))
        for _ in range(100):
            if record.exists():
                break
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert list(scratch.iterdir()) == []
    with pytest.raises(ProcessLookupError):
        os.kill(int(_record(record)["pid"][0]), 0)


@pytest.mark.parametrize("outcome", ["success", "exit", "timeout", "spawn"])
def test_the_real_home_is_untouched_and_scratch_is_gone_after_every_outcome(
    dirs: tuple[Path, Path, Path], outcome: str
) -> None:
    binary_dir, scratch, home = dirs
    for relative, text in {
        ".claude/settings.json": "{}",
        ".claude/.credentials.json": "real-claude",
        ".codex/config.toml": "model='x'",
        ".codex/auth.json": "real-codex",
    }.items():
        (home / relative).parent.mkdir(parents=True, exist_ok=True)
        (home / relative).write_text(text, encoding="utf-8")
    before = _tree(home)
    record = binary_dir / "record"
    options = {"exit": {"exit_code": 2}, "timeout": {"sleep": 30}}.get(outcome, {})
    claude = _fake(binary_dir, "claude", CLAUDE_STREAM, record, **options)  # type: ignore[arg-type]
    binary = Path("/nonexistent/claude") if outcome == "spawn" else claude
    asyncio.run(run_local_cli(_request(binary, scratch, home=home, timeout=1.0)))
    assert _tree(home) == before
    assert list(scratch.iterdir()) == []


def test_sign_in_never_appears_in_a_result_or_repr(dirs: tuple[Path, Path, Path]) -> None:
    binary_dir, scratch, home = dirs
    claude = _fake(binary_dir, "claude", CLAUDE_STREAM, binary_dir / "record")
    request = _request(claude, scratch, home=home)
    result = asyncio.run(run_local_cli(request))
    assert SECRET.decode() not in repr(result)
    assert SECRET.decode() not in repr(request.sign_in)


def test_codex_sign_in_is_read_only_when_it_is_a_chatgpt_sign_in(tmp_path: Path) -> None:
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "auth.json").write_text('{"auth_mode": "chatgpt", "tokens": {}}', encoding="utf-8")
    assert list(codex_sign_in({"CODEX_HOME": str(home)}).files) == [".codex/auth.json"]
    (home / "auth.json").write_text('{"auth_mode": "apikey"}', encoding="utf-8")
    with pytest.raises(SignInError, match="not a ChatGPT"):
        codex_sign_in({"CODEX_HOME": str(home)})
    with pytest.raises(SignInError, match="codex login"):
        codex_sign_in({"CODEX_HOME": str(tmp_path / "none")})


def test_claude_sign_in_file_missing_points_at_the_explicit_import(tmp_path: Path) -> None:
    with pytest.raises(SignInError, match="Keychain"):
        claude_sign_in({"HOME": str(tmp_path)})
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / ".credentials.json").write_bytes(SECRET)
    assert claude_sign_in({"HOME": str(tmp_path)}).files == {".claude/.credentials.json": SECRET}


def test_every_boundary_flag_is_one_the_recorded_cli_help_documents() -> None:
    """The flags that make up the Claude boundary exist in the CLI help of 2.1.296.

    A flag the CLI drops would fail the first trial, and a flag invented here would
    describe a boundary the CLI does not have. `--add-dir` is in the fixture as the way
    the boundary is widened, so the runner passing it would be visible.
    """
    help_text = (FIXTURES / "claude" / "help.flags.txt").read_text(encoding="utf-8")
    documented = set(re.findall(r"--([A-Za-z][A-Za-z-]*)", help_text))
    argv = ClaudeLocalAdapter().command("claude", "claude-opus-5-5", "prompt", Path("/w"))
    # `--` ends option parsing before the prompt; it is not a flag.
    passed = {token[2:] for token in argv if token.startswith("--") and token != "--"}
    boundary = {
        "restricted",
        "safe-mode",
        "strict-mcp-config",
        "permission-prompts",
        "tools",
        "allowedTools",
        "disallowedTools",
    }
    assert boundary <= passed, sorted(boundary - passed)
    assert boundary <= documented, sorted(boundary - documented)
    assert "--add-dir" not in passed
    # The fixture says it records the flags the runner sets. Hold it to that, so a flag
    # added to the command without re-capturing the help is caught here rather than at
    # the first trial.
    assert passed <= documented, sorted(passed - documented)


def test_adapters_have_distinct_runtimes() -> None:
    assert (ClaudeLocalAdapter().runtime, CodexLocalAdapter().runtime) == ("claude", "codex")
    with pytest.raises(ValueError):
        adapter_for_runtime("other")


def test_task_fixtures_start_at_the_workspace_root_and_only_changes_come_back(
    dirs: tuple[Path, Path, Path],
) -> None:
    binary_dir, scratch, home = dirs
    record = binary_dir / "record"
    claude = _fake(binary_dir, "claude", CLAUDE_STREAM, record)
    request = _request(claude, scratch, home=home)
    request = LocalCliRequest(
        **{**request.__dict__, "fixtures": {"greeting.py": b"fixture-body"}},
    )
    result = asyncio.run(run_local_cli(request))
    assert result.ok, result.detail
    assert _record(record)["fixture"] == ["fixture-body"]
    # The untouched fixture is not an output; only what the CLI wrote is.
    assert result.outputs == {"answer.md": b"done\n"}


def test_collect_outputs_returns_a_changed_fixture_and_skips_an_unchanged_one(
    tmp_path: Path,
) -> None:
    (tmp_path / "same.py").write_bytes(b"a")
    (tmp_path / "edited.py").write_bytes(b"new")
    found = _collect_outputs(tmp_path, {"same.py": b"a", "edited.py": b"old"})
    assert found == {"edited.py": b"new"}

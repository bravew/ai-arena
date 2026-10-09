"""Execution scorers: hidden tests, build, type-check, lint and security scan.

Each scorer copies a trial's stored artifacts into a fresh sandbox, adds the task's own files,
runs one command, and turns the result into a ``Score``. The sandbox is a small protocol so unit
tests can use a fake; the Docker backend implements the same two methods.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol, cast

from arena.core.models import Score
from arena.scorers.base import ScorerContext, ScorerError

VERSION = "1"
DEFAULT_TIMEOUT_S = 300.0
OUTPUT_TAIL_CHARS = 2000
# A shell reports these when it cannot start the command. That is our environment, not the model.
UNRUNNABLE_EXIT_CODES = frozenset({126, 127})

DEFAULT_PYTEST_COMMAND = (
    "python",
    "-m",
    "pytest",
    "-v",
    "--no-header",
    "-p",
    "no:cacheprovider",
)
DEFAULT_RUFF_COMMAND = ("python", "-m", "ruff", "check", "--output-format", "concise", ".")


class SandboxError(Exception):
    """The sandbox itself failed: it could not start, copy files or run the command."""


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class Sandbox(Protocol):
    """One isolated workspace. Paths are relative to its working directory."""

    def put_files(self, files: Mapping[str, bytes]) -> None:
        """Write files, creating parent directories. Raises ``SandboxError`` on failure."""
        ...

    def run(self, command: Sequence[str], *, timeout_s: float) -> CommandResult:
        """Run a command to completion or until the timeout. A timeout is a result, not an error."""
        ...


SandboxFactory = Callable[[], AbstractContextManager[Sandbox]]
FindingsCounter = Callable[[CommandResult], int]


class _SandboxScorer:
    """Shared flow: stage files, run one command in a fresh sandbox, classify failures."""

    def __init__(
        self,
        scorer_id: str,
        sandbox_factory: SandboxFactory,
        *,
        command: Sequence[str],
        timeout_s: float = DEFAULT_TIMEOUT_S,
        version: str = VERSION,
    ) -> None:
        self.id = scorer_id
        self.version = version
        self.sandbox_factory = sandbox_factory
        self.command = tuple(command)
        self.timeout_s = timeout_s

    def _run(
        self, context: ScorerContext, task_files: Mapping[str, bytes] | None = None
    ) -> tuple[CommandResult, list[str]]:
        """Return the command result and the contestant paths the task's files replaced."""
        task_files = task_files or {}
        files = {_safe_path(path): context.read(path) for path in sorted(context.artifacts)}
        overwritten = sorted(set(files) & set(task_files))
        files.update(task_files)  # task files win, so a contestant cannot edit its own tests
        try:
            with self.sandbox_factory() as sandbox:
                sandbox.put_files(files)
                result = sandbox.run(self.command, timeout_s=self.timeout_s)
        except SandboxError as exc:
            raise ScorerError(
                f"{self.id}: sandbox failed for trial {context.trial_id}: {exc}"
            ) from exc
        if not result.timed_out and result.exit_code in UNRUNNABLE_EXIT_CODES:
            raise ScorerError(
                f"{self.id}: sandbox could not run {self.command[0]!r} for trial "
                f"{context.trial_id} (exit {result.exit_code}): {_tail(result.stderr)}"
            )
        return result, overwritten

    def _score(
        self, context: ScorerContext, value: float, normalized: float, passed: bool,
        rationale: str, evidence: dict[str, Any],
    ) -> Score:  # fmt: skip
        return Score(
            trial_id=context.trial_id,
            scorer_id=self.id,
            scorer_version=self.version,
            value=value,
            normalized=normalized,
            passed=passed,
            rationale=rationale,
            evidence={"command": list(self.command), **evidence},
        )


class ExitCodeScorer(_SandboxScorer):
    """Pass when the command exits 0: a build or a type-check. Usable as a gate."""

    def score(self, context: ScorerContext) -> Score:
        result, _ = self._run(context)
        passed = not result.timed_out and result.exit_code == 0
        if result.timed_out:
            rationale = f"timed out after {self.timeout_s:g}s"
        else:
            rationale = f"exit code {result.exit_code}"
        evidence: dict[str, Any] = {"exit_code": result.exit_code, "timed_out": result.timed_out}
        if not passed:
            evidence["output_tail"] = _tail(result.stderr + result.stdout)
        return self._score(context, float(passed), float(passed), passed, rationale, evidence)


class FindingsScorer(_SandboxScorer):
    """Count findings (lint warnings, security issues). Zero scores 1.0, ``n`` scores 1/(1+n)."""

    def __init__(
        self,
        scorer_id: str,
        sandbox_factory: SandboxFactory,
        *,
        command: Sequence[str],
        count_findings: FindingsCounter,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        version: str = VERSION,
    ) -> None:
        super().__init__(
            scorer_id, sandbox_factory, command=command, timeout_s=timeout_s, version=version
        )
        self.count_findings = count_findings

    def score(self, context: ScorerContext) -> Score:
        result, _ = self._run(context)
        if result.timed_out:
            raise ScorerError(
                f"{self.id}: timed out after {self.timeout_s:g}s for trial {context.trial_id}; "
                "the finding count is unknown"
            )
        try:
            findings = self.count_findings(result)
        except ScorerError as exc:
            raise ScorerError(f"{self.id}: trial {context.trial_id}: {exc}") from exc
        return self._score(
            context,
            value=findings,
            normalized=1 / (1 + findings),
            passed=findings == 0,
            rationale=f"{findings} finding(s)",
            evidence={"findings": findings, "exit_code": result.exit_code},
        )


class HiddenTestsScorer(_SandboxScorer):
    """Run the task's hidden pytest files against the trial's artifacts.

    ``value`` and ``normalized`` are the pass rate over passed, failed and errored tests, so a
    solution that cannot be imported scores 0. A timeout scores 0. Output that has no pytest
    summary, or no tests, is an error: it says nothing about the contestant.
    """

    def __init__(
        self,
        sandbox_factory: SandboxFactory,
        *,
        tests: Mapping[str, bytes],
        command: Sequence[str] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        scorer_id: str = "hidden-tests",
        version: str = VERSION,
    ) -> None:
        if not tests:
            raise ScorerError(f"{scorer_id}: at least one hidden test file is required")
        self.tests = {_safe_path(path): data for path, data in tests.items()}
        argv = command or (
            *DEFAULT_PYTEST_COMMAND,
            *(path for path in sorted(self.tests) if path.endswith(".py")),
        )
        super().__init__(
            scorer_id, sandbox_factory, command=argv, timeout_s=timeout_s, version=version
        )

    def score(self, context: ScorerContext) -> Score:
        result, overwritten = self._run(context, self.tests)
        base: dict[str, Any] = {"overwritten_paths": overwritten, "exit_code": result.exit_code}
        if result.timed_out:
            return self._score(
                context, 0.0, 0.0, False, f"timed out after {self.timeout_s:g}s",
                {**base, "timed_out": True},
            )  # fmt: skip
        run = _parse_pytest(result, self.id, context.trial_id)
        ran = run.passed + run.failed + run.errors
        rate = run.passed / ran
        return self._score(
            context,
            value=rate,
            normalized=rate,
            passed=run.failed == 0 and run.errors == 0,
            rationale=f"{run.passed} of {ran} hidden tests passed",
            evidence={
                **base,
                "timed_out": False,
                "passed": run.passed,
                "failed": run.failed,
                "errors": run.errors,
                "skipped": run.skipped,
                "skipped_tests": run.skipped_tests,
                "passed_tests": run.passed_tests,
                "failed_tests": run.failed_tests,
            },
        )


def build_scorer(
    sandbox_factory: SandboxFactory,
    *,
    command: Sequence[str],
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ExitCodeScorer:
    return ExitCodeScorer("build", sandbox_factory, command=command, timeout_s=timeout_s)


def typecheck_scorer(
    sandbox_factory: SandboxFactory,
    *,
    command: Sequence[str],
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ExitCodeScorer:
    return ExitCodeScorer("typecheck", sandbox_factory, command=command, timeout_s=timeout_s)


def lint_scorer(
    sandbox_factory: SandboxFactory,
    *,
    command: Sequence[str] = DEFAULT_RUFF_COMMAND,
    count_findings: FindingsCounter | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> FindingsScorer:
    return FindingsScorer(
        "lint",
        sandbox_factory,
        command=command,
        count_findings=count_findings or count_ruff_findings,
        timeout_s=timeout_s,
    )


def security_scorer(
    sandbox_factory: SandboxFactory,
    *,
    command: Sequence[str],
    count_findings: FindingsCounter | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> FindingsScorer:
    """``command`` must print JSON with a ``results`` list, as ``bandit -f json`` and
    ``semgrep --json`` do."""
    return FindingsScorer(
        "security",
        sandbox_factory,
        command=command,
        count_findings=count_findings or count_json_results,
        timeout_s=timeout_s,
    )


# --- output parsing --------------------------------------------------------------------

_RUFF_FOUND = re.compile(r"^Found (\d+) errors?\.$", re.MULTILINE)


def count_ruff_findings(result: CommandResult) -> int:
    """Read ruff's ``Found N errors.`` or ``All checks passed!`` line. Anything else is unknown."""
    output = result.stdout + "\n" + result.stderr
    if found := _RUFF_FOUND.search(output):
        return int(found.group(1))
    if "All checks passed!" in output:
        return 0
    raise ScorerError(f"cannot read ruff output (exit {result.exit_code}): {_tail(output)}")


def count_json_results(result: CommandResult) -> int:
    """Length of the ``results`` list in a JSON report on stdout."""
    try:
        report: object = json.loads(result.stdout)
    except ValueError:
        report = None
    results = cast("dict[str, object]", report).get("results") if isinstance(report, dict) else None
    if not isinstance(results, list):
        raise ScorerError(f"no JSON report with a results list (exit {result.exit_code})")
    return len(cast("list[object]", results))


@dataclass(frozen=True)
class _PytestRun:
    passed: int
    failed: int
    errors: int
    skipped: int
    skipped_tests: list[str]
    passed_tests: list[str]
    failed_tests: list[str]


_PYTEST_SUMMARY = re.compile(
    r"^(?:=+ )?(?P<counts>\d+ [a-z]+(?:, \d+ [a-z]+)*) in [\d.]+s(?: \([\d:]+\))?(?: =+)?$",
    re.MULTILINE,
)
_PYTEST_COUNT = re.compile(r"(\d+) ([a-z]+)")
_PYTEST_RESULT_LINE = re.compile(
    r"^(?:(PASSED|FAILED|ERROR|SKIPPED) (\S+)|(\S+) (PASSED|FAILED|ERROR|SKIPPED))",
    re.MULTILINE,
)
_PYTEST_VERBOSE_LINE = re.compile(
    r"^\s*(?:(\S+)\s+(PASSED|FAILED|ERROR|SKIPPED)(?:\s+\[[^]]+\])?|"
    r"(PASSED|FAILED|ERROR|SKIPPED)\s+(\S+))\s*$",
    re.MULTILINE,
)


def _parse_pytest(result: CommandResult, scorer_id: str, trial_id: str) -> _PytestRun:
    summary = _PYTEST_SUMMARY.search(result.stdout)
    if summary is None:
        raise ScorerError(
            f"{scorer_id}: no pytest summary for trial {trial_id} (exit {result.exit_code}): "
            f"{_tail(result.stdout + result.stderr)}"
        )
    counts: dict[str, int] = {}
    for number, word in _PYTEST_COUNT.findall(summary.group("counts")):
        counts[word.removesuffix("s")] = counts.get(word.removesuffix("s"), 0) + int(number)
    passed, failed, errors = (
        counts.get("passed", 0),
        counts.get("failed", 0),
        counts.get("error", 0),
    )

    passed_tests: list[str] = []
    failed_tests: list[str] = []
    skipped_tests: list[str] = []
    for leading_id, leading_status, trailing_status, trailing_id in _PYTEST_VERBOSE_LINE.findall(
        result.stdout
    ):
        test_id = leading_id or trailing_id
        status = leading_status or trailing_status
        if status == "PASSED":
            passed_tests.append(test_id)
        elif status == "SKIPPED":
            skipped_tests.append(test_id)
        else:
            failed_tests.append(test_id)

    if passed + failed + errors == 0:
        raise ScorerError(f"{scorer_id}: pytest ran no tests for trial {trial_id}")
    if (result.exit_code == 0) != (failed + errors == 0):
        raise ScorerError(
            f"{scorer_id}: pytest exit code {result.exit_code} contradicts its summary "
            f"{summary.group('counts')!r} for trial {trial_id}"
        )
    if (
        len(passed_tests) != passed
        or len(failed_tests) != failed + errors
        or len(skipped_tests) != counts.get("skipped", 0)
    ):
        raise ScorerError(
            f"{scorer_id}: pytest result lines disagree with its summary for trial {trial_id}"
        )
    return _PytestRun(
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=counts.get("skipped", 0),
        skipped_tests=sorted(skipped_tests),
        passed_tests=sorted(passed_tests),
        failed_tests=sorted(failed_tests),
    )


def _safe_path(path: str) -> str:
    """A relative path that stays inside the workspace."""
    parts = PurePosixPath(path).parts
    if not path or path.startswith("/") or ".." in parts:
        raise ScorerError(f"unsafe artifact path {path!r}")
    return path


def _tail(text: str) -> str:
    return text[-OUTPUT_TAIL_CHARS:]

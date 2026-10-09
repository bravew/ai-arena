from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from arena.core.cas import ArtifactStore
from arena.core.models import Artifact
from arena.scorers.base import (
    Scorer,
    ScorerContext,
    ScorerError,
    aggregate_scores,
    validate_score,
)
from arena.scorers.execution import (
    CommandResult,
    FindingsScorer,
    HiddenTestsScorer,
    SandboxError,
    build_scorer,
    lint_scorer,
    security_scorer,
    typecheck_scorer,
)
from arena.scorers.registry import ScorerRegistry

GOLDEN = Path(__file__).parent / "golden"
BUGGY_TESTS = {
    "test_calc.py::test_mean_basic",
    "test_calc.py::test_mean_negative",
    "test_calc.py::test_mean_empty_raises",
}

PYTEST_ALL_PASS = "PASSED test_calc.py::test_a\nPASSED test_calc.py::test_b\n2 passed in 0.01s\n"


def load_fixtures(directory: Path) -> dict[str, bytes]:
    """Read a golden directory; a ``.fixture`` suffix keeps the files out of lint and type-check."""
    return {
        path.relative_to(directory).as_posix().removesuffix(".fixture"): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


class DirSandbox:
    """Runs commands in a throwaway directory on the host. Test double only: no isolation."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def put_files(self, files: Mapping[str, bytes]) -> None:
        for name, data in files.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def run(self, command: Sequence[str], *, timeout_s: float) -> CommandResult:
        argv = [sys.executable if part == "python" else part for part in command]
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.root), "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            done = subprocess.run(
                argv, cwd=self.root, env=env, capture_output=True, text=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired:
            return CommandResult(exit_code=-1, timed_out=True)
        return CommandResult(done.returncode, done.stdout, done.stderr)


@contextmanager
def dir_sandbox():
    with tempfile.TemporaryDirectory() as root:
        yield DirSandbox(Path(root))


class FakeSandbox:
    """Records what was copied in and run, and replies with canned results."""

    def __init__(self, *results: CommandResult | SandboxError) -> None:
        self.results = list(results)
        self.created = 0
        self.files: dict[str, bytes] = {}
        self.commands: list[tuple[str, ...]] = []
        self.timeouts: list[float] = []

    @contextmanager
    def __call__(self):
        self.created += 1
        yield self

    def put_files(self, files: Mapping[str, bytes]) -> None:
        self.files.update(files)

    def run(self, command: Sequence[str], *, timeout_s: float) -> CommandResult:
        self.commands.append(tuple(command))
        self.timeouts.append(timeout_s)
        result = self.results.pop(0)
        if isinstance(result, SandboxError):
            raise result
        return result


def make_context(tmp_path: Path, files: Mapping[str, bytes] | None = None) -> ScorerContext:
    blobs = ArtifactStore(tmp_path / "artifacts")
    artifacts = {
        path: Artifact(sha256=blobs.put(data), path=path, mime="text/x-python", render_hint="code")
        for path, data in (files or {}).items()
    }
    return ScorerContext(trial_id="trial-1", artifacts=artifacts, blobs=blobs)


def hidden_scorer(factory) -> HiddenTestsScorer:
    return HiddenTestsScorer(factory, tests=load_fixtures(GOLDEN / "calc" / "hidden"))


# --- golden fixtures, run for real in a throwaway directory ------------------------------


def test_oracle_solution_scores_one(tmp_path: Path) -> None:
    context = make_context(tmp_path, load_fixtures(GOLDEN / "calc" / "oracle"))
    score = hidden_scorer(dir_sandbox).score(context)
    assert score.normalized == 1.0
    assert score.passed is True
    assert score.evidence["passed"] == 4
    assert score.evidence["failed_tests"] == []


def test_null_solution_scores_zero(tmp_path: Path) -> None:
    score = hidden_scorer(dir_sandbox).score(make_context(tmp_path))
    assert score.normalized == 0.0
    assert score.passed is False
    assert score.evidence["errors"] == 1


def test_buggy_solution_fails_the_expected_tests(tmp_path: Path) -> None:
    context = make_context(tmp_path, load_fixtures(GOLDEN / "calc" / "buggy"))
    score = hidden_scorer(dir_sandbox).score(context)
    assert set(score.evidence["failed_tests"]) == BUGGY_TESTS
    assert score.evidence["passed_tests"] == ["test_calc.py::test_clamp_bounds"]
    assert score.normalized == 0.25
    assert score.passed is False


def test_scoring_twice_gives_identical_scores(tmp_path: Path) -> None:
    context = make_context(tmp_path, load_fixtures(GOLDEN / "calc" / "buggy"))
    scorer = hidden_scorer(dir_sandbox)
    assert scorer.score(context) == scorer.score(context)


def test_lint_counts_findings_with_real_ruff(tmp_path: Path) -> None:
    scorer = lint_scorer(dir_sandbox, command=("python", "-m", "ruff", "check", "--no-cache", "."))
    clean = scorer.score(
        make_context(tmp_path, {"clean.py": load_fixtures(GOLDEN / "lint")["clean.py"]})
    )
    dirty = scorer.score(
        make_context(tmp_path, {"dirty.py": load_fixtures(GOLDEN / "lint")["dirty.py"]})
    )
    assert (clean.normalized, clean.passed) == (1.0, True)
    assert dirty.value == 2
    assert dirty.normalized == pytest.approx(1 / 3)
    assert dirty.passed is False


# --- hidden tests: sandbox contract and failure classes ----------------------------------


def test_hidden_tests_replace_a_contestant_file_at_the_same_path(tmp_path: Path) -> None:
    fake = FakeSandbox(CommandResult(0, PYTEST_ALL_PASS))
    tests = {"test_calc.py": b"hidden"}
    context = make_context(tmp_path, {"test_calc.py": b"def test_x(): pass", "calc.py": b"x"})
    score = HiddenTestsScorer(fake, tests=tests).score(context)
    assert fake.files == {"test_calc.py": b"hidden", "calc.py": b"x"}
    assert score.evidence["overwritten_paths"] == ["test_calc.py"]
    assert fake.commands[0][-1] == "test_calc.py"


def test_each_score_gets_a_fresh_sandbox(tmp_path: Path) -> None:
    fake = FakeSandbox(CommandResult(0, PYTEST_ALL_PASS), CommandResult(0, PYTEST_ALL_PASS))
    scorer = HiddenTestsScorer(fake, tests={"test_calc.py": b"hidden"})
    scorer.score(make_context(tmp_path))
    scorer.score(make_context(tmp_path))
    assert fake.created == 2


def test_a_timeout_scores_zero_and_is_recorded(tmp_path: Path) -> None:
    fake = FakeSandbox(CommandResult(-1, timed_out=True))
    scorer = HiddenTestsScorer(fake, tests={"test_calc.py": b"hidden"}, timeout_s=7)
    score = scorer.score(make_context(tmp_path))
    assert (score.normalized, score.passed, score.evidence["timed_out"]) == (0.0, False, True)
    assert fake.timeouts == [7]


@pytest.mark.parametrize(
    "outcome",
    [
        SandboxError("container died"),
        CommandResult(127, "", "python: command not found"),
        CommandResult(1, "", "No module named pytest"),
        CommandResult(5, "no tests ran in 0.01s\n"),
        CommandResult(0, "3 passed in 0.01s\n"),
        CommandResult(1, "1 skipped in 0.01s\n"),
        CommandResult(0, "PASSED a.py::t\n1 passed, 1 failed in 0.01s\n"),
    ],
)
def test_infrastructure_and_unreadable_results_are_errors_not_zero(
    tmp_path: Path, outcome: CommandResult | SandboxError
) -> None:
    scorer = HiddenTestsScorer(FakeSandbox(outcome), tests={"test_calc.py": b"hidden"})
    with pytest.raises(ScorerError):
        scorer.score(make_context(tmp_path))


@pytest.mark.parametrize("path", ["../escape.py", "/abs.py", "a/../../b.py", ""])
def test_artifact_paths_cannot_leave_the_workspace(tmp_path: Path, path: str) -> None:
    fake = FakeSandbox(CommandResult(0, PYTEST_ALL_PASS))
    scorer = HiddenTestsScorer(fake, tests={"test_calc.py": b"hidden"})
    with pytest.raises(ScorerError, match="unsafe"):
        scorer.score(make_context(tmp_path, {path: b"x"}))
    assert fake.files == {}


# --- build, type-check, lint, security ---------------------------------------------------


@pytest.mark.parametrize("factory", [build_scorer, typecheck_scorer])
def test_exit_code_scorers_pass_on_zero_and_fail_otherwise(tmp_path: Path, factory) -> None:
    fake = FakeSandbox(
        CommandResult(0, "ok"),
        CommandResult(1, "", "error: bad"),
        CommandResult(-1, timed_out=True),
    )
    scorer = factory(fake, command=("make",))
    ok, bad, hung = (scorer.score(make_context(tmp_path)) for _ in range(3))
    assert (ok.normalized, ok.passed) == (1.0, True)
    assert (bad.normalized, bad.passed) == (0.0, False)
    assert "error: bad" in bad.evidence["output_tail"]
    assert (hung.normalized, hung.passed, hung.evidence["timed_out"]) == (0.0, False, True)
    assert fake.commands == [("make",)] * 3


def test_a_command_the_sandbox_cannot_run_is_an_error_not_a_failed_build(tmp_path: Path) -> None:
    scorer = build_scorer(FakeSandbox(CommandResult(127, "", "make: not found")), command=("make",))
    with pytest.raises(ScorerError, match="could not run"):
        scorer.score(make_context(tmp_path))


def test_failed_build_gate_caps_the_aggregate(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    failed = build_scorer(FakeSandbox(CommandResult(2)), command=("make",)).score(context)
    passed = hidden_scorer(FakeSandbox(CommandResult(0, PYTEST_ALL_PASS))).score(context)
    assert aggregate_scores([failed, passed], gates={"build"}) == 0.3


def test_security_counts_json_results(tmp_path: Path) -> None:
    findings = json.dumps({"results": [{"test_id": "B101"}, {"test_id": "B602"}]})
    fake = FakeSandbox(
        CommandResult(0, json.dumps({"results": []})),
        CommandResult(1, findings, "[main] INFO profile"),
    )
    scorer = security_scorer(fake, command=("bandit", "-r", ".", "-f", "json"))
    clean, flagged = scorer.score(make_context(tmp_path)), scorer.score(make_context(tmp_path))
    assert (clean.normalized, clean.passed) == (1.0, True)
    assert flagged.value == 2
    assert flagged.normalized == pytest.approx(1 / 3)
    assert flagged.passed is False


@pytest.mark.parametrize("stdout", ["", "not json", "[]", '{"errors": []}'])
def test_unreadable_findings_output_is_an_error_not_clean(tmp_path: Path, stdout: str) -> None:
    scorer = security_scorer(FakeSandbox(CommandResult(0, stdout)), command=("bandit",))
    with pytest.raises(ScorerError, match="trial-1"):
        scorer.score(make_context(tmp_path))


# --- framework integration ---------------------------------------------------------------


def test_scorers_register_and_return_their_own_identity(tmp_path: Path) -> None:
    fake = FakeSandbox(CommandResult(0, PYTEST_ALL_PASS), CommandResult(0))
    scorers: list[Scorer] = [
        HiddenTestsScorer(fake, tests={"test_calc.py": b"hidden"}),
        build_scorer(fake, command=("make",)),
        typecheck_scorer(fake, command=("pyright",)),
        lint_scorer(fake, command=("ruff",)),
        security_scorer(fake, command=("bandit",)),
    ]
    registry = ScorerRegistry(scorers)
    assert len(registry) == 5
    assert registry.get("hidden-tests@1") is scorers[0]
    context = make_context(tmp_path)
    score = validate_score(scorers[1], context, scorers[1].score(context))
    assert (score.scorer_id, score.scorer_version) == ("build", "1")


def test_findings_scorer_takes_a_custom_counter(tmp_path: Path) -> None:
    scorer = FindingsScorer(
        "typecheck-count",
        FakeSandbox(CommandResult(1, "a\nb\nc")),
        command=("tool",),
        count_findings=lambda result: len(result.stdout.splitlines()),
    )
    assert scorer.score(make_context(tmp_path)).value == 3

import os
from pathlib import Path

import pytest

from tests.testenv import guard
from tests.testenv.plugin import TempEnv


def test_home_is_temporary(testenv: TempEnv) -> None:
    assert Path.home() == testenv.home
    assert Path.home() != guard.REAL_HOME
    assert os.environ["CLAUDE_CONFIG_DIR"] == str(testenv.home / ".claude")
    assert os.environ["CODEX_HOME"] == str(testenv.home / ".codex")


def test_provider_secrets_are_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Set after the fixture ran, so check the rule the fixture applies.
    from tests.testenv.plugin import SECRET_NAMES, SECRET_SUFFIXES

    assert not [n for n in os.environ if n.endswith(SECRET_SUFFIXES) or n in SECRET_NAMES]


INNER_TESTS = """
import os
from tests.testenv.guard import REAL_HOME

def test_clean(tmp_path):
    (tmp_path / "ok.txt").write_text("fine")

def test_writes_real_home():
    with open(REAL_HOME / "leak.txt", "w") as f:
        f.write("x")

def test_swallows_the_guard():
    try:
        os.mkdir(REAL_HOME / "leak-dir")
    except BaseException:
        pass

def test_reads_real_agent_config():
    os.listdir(REAL_HOME / ".claude")
"""


def test_guard_fails_tests_that_touch_the_real_home(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, testenv: TempEnv
) -> None:
    # The inner run's "real HOME" is this test's temporary HOME, so nothing real is touched.
    (testenv.home / ".claude").mkdir()
    monkeypatch.setenv("PYTHONPATH", str(guard.REPO_ROOT))
    pytester.makepyfile(test_inner=INNER_TESTS)
    result = pytester.runpytest_subprocess("-p", "tests.testenv.plugin", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=2, failed=2, errors=3)
    result.stdout.fnmatch_lines(["*write under the real HOME*leak.txt*"])
    result.stdout.fnmatch_lines(["*read of real agent or arena state*.claude*"])
    assert not (testenv.home / "leak.txt").exists()
    assert not (testenv.home / "leak-dir").exists()

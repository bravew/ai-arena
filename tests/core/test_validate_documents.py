from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app

runner = CliRunner()


def write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def validate(*paths: Path) -> tuple[int, str]:
    result = runner.invoke(app, ["validate", *map(str, paths)])
    return result.exit_code, result.output


TASK = "id: t.one\nversion: 1\nkind: codegen\nprompt_file: prompt.md\ncreated_at: 2026-10-08\n"


def test_task_with_its_prompt_validates(tmp_path: Path) -> None:
    write(tmp_path, "t/prompt.md", "Do it.")
    code, _ = validate(write(tmp_path, "t/task.yaml", TASK))
    assert code == 0


def test_task_without_its_prompt_file_is_rejected(tmp_path: Path) -> None:
    code, output = validate(write(tmp_path, "t/task.yaml", TASK))
    assert code == 1
    assert "prompt.md does not exist" in output


def test_task_with_unknown_kind_is_rejected(tmp_path: Path) -> None:
    write(tmp_path, "t/prompt.md", "x")
    code, output = validate(write(tmp_path, "t/task.yaml", TASK.replace("codegen", "poetry")))
    assert code == 1
    assert "kind" in output


KIT = """id: k
version: 1
instructions: instructions.md
skills:
  - path: skills/one
mcp:
  - name: docs
    url: https://mcp.invalid/docs
    headers: { Authorization: "Bearer ${DOCS_TOKEN}" }
"""


def test_kit_accepts_a_bearer_env_reference(tmp_path: Path) -> None:
    write(tmp_path, "k/instructions.md", "be nice")
    write(tmp_path, "k/skills/one/SKILL.md", "skill")
    code, output = validate(write(tmp_path, "k/kit.yaml", KIT))
    assert code == 0, output


def test_kit_rejects_a_plaintext_bearer_token(tmp_path: Path) -> None:
    write(tmp_path, "k/instructions.md", "x")
    write(tmp_path, "k/skills/one/SKILL.md", "x")
    code, output = validate(
        write(tmp_path, "k/kit.yaml", KIT.replace("${DOCS_TOKEN}", "sk-live-123"))
    )
    assert code == 1
    assert "environment references" in output


def test_kit_with_missing_files_is_rejected(tmp_path: Path) -> None:
    code, output = validate(write(tmp_path, "k/kit.yaml", KIT))
    assert code == 1
    assert "instructions.md does not exist" in output
    assert "skills/one is not a directory" in output


def test_contestant_validates_and_bad_model_is_rejected(tmp_path: Path) -> None:
    good = "id_label: a\nmodel: mock/echo\nparams: { temperature: 0 }\nscaffold: none\n"
    assert validate(write(tmp_path, "contestants/a.yaml", good))[0] == 0
    code, output = validate(
        write(tmp_path, "contestants/b.yaml", good.replace("mock/echo", "echo"))
    )
    assert code == 1
    assert "provider/model" in output


def test_contestant_matrix_is_expanded_during_validation(tmp_path: Path) -> None:
    matrix = "matrix:\n  model: [mock/echo, mock/canned]\n  scaffold: [none]\n"
    assert validate(write(tmp_path, "contestants/m.yaml", matrix))[0] == 0
    bad = "matrix:\n  model: []\n  scaffold: [none]\n"
    code, output = validate(write(tmp_path, "contestants/n.yaml", bad))
    assert code == 1
    assert "matrix.model" in output


def test_rubric_needs_anchors(tmp_path: Path) -> None:
    ok = "id: r\nversion: 1\nkind: content\nanchors:\n  clarity:\n    0: bad\n    2: good\n"
    assert validate(write(tmp_path, "rubrics/r.yaml", ok))[0] == 0
    code, output = validate(write(tmp_path, "rubrics/s.yaml", "id: r\nversion: 1\nkind: content\n"))
    assert code == 1
    assert "anchors" in output


@pytest.mark.parametrize("name", ["random.yaml", "suites/x/suite.yaml"])
def test_unknown_yaml_is_still_reported(tmp_path: Path, name: str) -> None:
    code, output = validate(write(tmp_path, name, "a: 1\n"))
    assert code == 1
    assert "unsupported config file" in output

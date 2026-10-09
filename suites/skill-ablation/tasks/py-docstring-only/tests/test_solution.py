import ast
from pathlib import Path

import pytest
from solution import clamp

SOURCE = Path(__file__).resolve().parent.parent / "solution.py"
BODY = """
if lower > upper:
    raise ValueError("lower must not be greater than upper")
return max(lower, min(value, upper))
"""
EXPECTED_BODY = [ast.dump(statement) for statement in ast.parse(BODY).body]


def function_node() -> ast.FunctionDef:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert [node.name for node in nodes] == ["clamp"]
    return nodes[0]


def test_behavior_is_unchanged() -> None:
    assert clamp(5, 1, 10) == 5
    assert clamp(-3, 1, 10) == 1
    assert clamp(99, 1, 10) == 10
    assert clamp(1, 1, 10) == 1
    assert clamp(10, 1, 10) == 10
    with pytest.raises(ValueError):
        clamp(1, 5, 2)


def test_implementation_statements_are_unchanged() -> None:
    node = function_node()
    assert [arg.arg for arg in node.args.args] == ["value", "lower", "upper"]
    statements = node.body[1:] if ast.get_docstring(node) is not None else node.body
    assert [ast.dump(statement) for statement in statements] == EXPECTED_BODY


def test_docstring_covers_bounds_requirement_and_return() -> None:
    docstring = ast.get_docstring(function_node())
    assert docstring is not None
    text = docstring.lower()
    assert "inclusive" in text
    assert "valueerror" in text
    assert "lower" in text and "upper" in text
    assert "return" in text

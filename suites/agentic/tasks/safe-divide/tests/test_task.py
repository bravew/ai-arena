from math_utils import safe_divide


def test_repository_change() -> None:
    assert safe_divide(9, 3) == 3
    try:
        safe_divide(1, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero divisor must raise")

from solution import factorial


def test_contract() -> None:
    assert factorial(0) == 1
    assert factorial(5) == 120
    try:
        factorial(-1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative input must raise ValueError")

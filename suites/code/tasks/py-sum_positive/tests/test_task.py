from solution import sum_positive


def test_contract() -> None:
    assert sum_positive([-2, 0, 3, 4]) == 7
    assert sum_positive([]) == 0

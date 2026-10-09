from solution import add


def test_adds_positive_numbers() -> None:
    assert add(2, 3) == 5


def test_adds_negative_numbers() -> None:
    assert add(-4, 1) == -3

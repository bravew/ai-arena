from solution import unique_sorted


def test_contract() -> None:
    values = [3, 1, 3, -2]
    assert unique_sorted(values) == [-2, 1, 3]
    assert values == [3, 1, 3, -2]

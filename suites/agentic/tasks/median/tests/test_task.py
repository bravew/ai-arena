from statistics_utils import median


def test_repository_change() -> None:
    assert median([5, 1, 3]) == 3
    assert median([1, 2, 3, 4]) == 2.5

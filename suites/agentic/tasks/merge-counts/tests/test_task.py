from counters import merge_counts


def test_repository_change() -> None:
    left = {"a": 2}
    right = {"a": 3, "b": 1}
    assert merge_counts(left, right) == {"a": 5, "b": 1}
    assert left == {"a": 2}

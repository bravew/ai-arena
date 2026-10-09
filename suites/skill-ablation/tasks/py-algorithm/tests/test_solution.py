from solution import merge_intervals


def test_empty_and_single() -> None:
    assert merge_intervals([]) == []
    assert merge_intervals([(2, 4)]) == [[2, 4]]


def test_sorts_unordered_input() -> None:
    assert merge_intervals([[8, 9], [1, 2], [4, 5]]) == [[1, 2], [4, 5], [8, 9]]


def test_merges_overlapping_and_touching() -> None:
    assert merge_intervals([[1, 3], [2, 6]]) == [[1, 6]]
    assert merge_intervals([[1, 3], [3, 5]]) == [[1, 5]]


def test_nested_interval_is_absorbed() -> None:
    assert merge_intervals([[1, 10], [2, 3], [4, 5]]) == [[1, 10]]
    assert merge_intervals([[5, 6], [1, 10]]) == [[1, 10]]


def test_chain_of_merges_and_duplicates() -> None:
    assert merge_intervals([[1, 2], [2, 3], [3, 4], [3, 4], [6, 7]]) == [[1, 4], [6, 7]]


def test_does_not_modify_input() -> None:
    data = [[5, 6], [1, 3], [2, 4]]
    snapshot = [list(item) for item in data]
    merge_intervals(data)
    assert data == snapshot


def test_returns_new_lists() -> None:
    data = [[1, 2]]
    result = merge_intervals(data)
    result[0][1] = 99
    assert data == [[1, 2]]

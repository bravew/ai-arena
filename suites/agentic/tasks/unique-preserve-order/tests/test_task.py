from collections_utils import unique_items


def test_repository_change() -> None:
    values = ["b", "a", "b"]
    assert unique_items(values) == ["b", "a"]
    assert values == ["b", "a", "b"]

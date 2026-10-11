from files import dedupe_paths


def test_repository_change() -> None:
    assert dedupe_paths(["a//b", "a/b", "/c/"]) == ["a/b", "c"]

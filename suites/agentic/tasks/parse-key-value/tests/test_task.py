from settings import parse_pairs


def test_repository_change() -> None:
    assert parse_pairs([" a = 1 ", "", "b=x=y"]) == {"a": "1", "b": "x=y"}

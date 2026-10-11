from text_utils import title_words


def test_repository_change() -> None:
    assert title_words("  hello   WORLD ") == "Hello World"

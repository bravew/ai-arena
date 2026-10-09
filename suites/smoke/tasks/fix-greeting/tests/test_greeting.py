from greeting import greet


def test_greets_by_name() -> None:
    assert greet("Ada") == "Hello, Ada!"


def test_greets_another_name() -> None:
    assert greet("Grace") == "Hello, Grace!"

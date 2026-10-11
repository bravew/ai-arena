from solution import slugify


def test_contract() -> None:
    assert slugify("Hello, Arena!") == "hello-arena"
    assert slugify("  A  B ") == "a-b"
    assert slugify("---") == ""

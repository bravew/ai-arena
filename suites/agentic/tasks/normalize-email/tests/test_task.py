from contact import normalize_email


def test_repository_change() -> None:
    assert normalize_email(" A@EXAMPLE.COM ") == "a@example.com"

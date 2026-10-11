from solution import format_name


def test_already_normalized() -> None:
    assert format_name("Ada", "Lovelace") == "Lovelace, Ada"


def test_trims_and_collapses_whitespace() -> None:
    assert format_name("  Mary \t Jane ", "van   der\nBerg ") == "van der Berg, Mary Jane"


def test_preserves_case() -> None:
    assert format_name("mcKENZIE", "o'NEIL") == "o'NEIL, mcKENZIE"


def test_empty_components() -> None:
    assert format_name("", "Lovelace") == "Lovelace"
    assert format_name("Ada", "   ") == "Ada"
    assert format_name(" ", "") == ""

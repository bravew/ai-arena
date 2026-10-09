from display import format_bytes


def test_repository_change() -> None:
    assert format_bytes(8) == "8 B"
    assert format_bytes(1536) == "1.5 KiB"

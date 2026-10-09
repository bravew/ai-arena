from dates import parse_iso_date


def test_repository_change() -> None:
    from datetime import date
    assert parse_iso_date("2024-02-29") == date(2024, 2, 29)

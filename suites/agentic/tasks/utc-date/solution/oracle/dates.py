from datetime import date


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)

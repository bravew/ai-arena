from pathlib import PurePath


def safe_filename(value: str) -> str:
    return PurePath(value).name

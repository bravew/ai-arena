def format_bytes(size: int) -> str:
    if size < 0:
        raise ValueError("size must be nonnegative")
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"

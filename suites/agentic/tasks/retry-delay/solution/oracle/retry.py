def retry_delay(attempt: int) -> int:
    if attempt < 1:
        raise ValueError("attempt must be positive")
    return 2 ** (attempt - 1)

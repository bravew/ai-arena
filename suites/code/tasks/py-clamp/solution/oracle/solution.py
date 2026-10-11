def clamp(value: int, lower: int, upper: int) -> int:
    """Return value bounded inclusively by lower and upper."""
    if lower > upper:
        raise ValueError("lower exceeds upper")
    return min(max(value, lower), upper)

def clamp(value, lower, upper):
    """Clamp a value."""
    if lower > upper:
        raise ValueError("lower must not be greater than upper")
    return max(lower, min(value, upper))

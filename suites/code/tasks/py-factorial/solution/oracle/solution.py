def factorial(value: int) -> int:
    """Return value factorial; reject negative values with ValueError."""
    if value < 0:
        raise ValueError("negative input")
    result = 1
    for factor in range(2, value + 1):
        result *= factor
    return result

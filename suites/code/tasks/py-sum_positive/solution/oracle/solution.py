def sum_positive(values: list[int]) -> int:
    """Return the sum of strictly positive values."""
    return sum(value for value in values if value > 0)

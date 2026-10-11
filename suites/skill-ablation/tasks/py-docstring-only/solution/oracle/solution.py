def clamp(value, lower, upper):
    """Return `value` limited to the inclusive range `lower` to `upper`.

    Both bounds are inclusive: a value equal to either bound is returned
    unchanged. A value below `lower` returns `lower`, and a value above `upper`
    returns `upper`.

    Raises:
        ValueError: if `lower` is greater than `upper`.
    """
    if lower > upper:
        raise ValueError("lower must not be greater than upper")
    return max(lower, min(value, upper))

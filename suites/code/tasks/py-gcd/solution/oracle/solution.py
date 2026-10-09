def gcd(left: int, right: int) -> int:
    """Return the non-negative greatest common divisor."""
    left, right = abs(left), abs(right)
    while right:
        left, right = right, left % right
    return left

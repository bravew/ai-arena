def safe_divide(left: float, right: float) -> float:
    if right == 0:
        raise ValueError("division by zero")
    return left / right

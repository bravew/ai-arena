def median(values: list[float]) -> float:
    if not values:
        raise ValueError("empty input")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2

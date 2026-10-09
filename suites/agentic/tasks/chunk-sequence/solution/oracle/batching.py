def chunks(values: list[int], size: int) -> list[list[int]]:
    if size <= 0:
        raise ValueError("size must be positive")
    return [values[index : index + size] for index in range(0, len(values), size)]

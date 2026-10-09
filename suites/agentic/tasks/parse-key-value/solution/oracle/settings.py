def parse_pairs(lines: list[str]) -> dict[str, str]:
    result = {}
    for line in lines:
        if not line.strip():
            continue
        if "=" not in line:
            raise ValueError("expected key=value")
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result

def parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise ValueError("invalid port") from error
    if not 1 <= port <= 65535:
        raise ValueError("port out of range")
    return port

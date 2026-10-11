def parse_port(text):
    if not (text.isascii() and text.isdigit()):
        raise ValueError(f"not a port number: {text!r}")
    port = int(text)
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range: {text!r}")
    return port

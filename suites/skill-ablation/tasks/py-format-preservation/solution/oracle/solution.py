def format_name(first, last):
    first = " ".join(first.split())
    last = " ".join(last.split())
    if first and last:
        return f"{last}, {first}"
    return first or last

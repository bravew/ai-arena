def dedupe_paths(paths: list[str]) -> list[str]:
    return list(dict.fromkeys("/".join(part for part in path.split("/") if part) for path in paths))

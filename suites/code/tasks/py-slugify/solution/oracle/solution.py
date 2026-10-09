def slugify(text: str) -> str:
    """Lowercase words and join runs of non-alphanumerics with hyphens."""
    import re

    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

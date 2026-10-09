def reverse_words(text: str) -> str:
    """Reverse word order and normalize whitespace to single spaces."""
    return " ".join(reversed(text.split()))

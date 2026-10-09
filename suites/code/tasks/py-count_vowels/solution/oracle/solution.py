def count_vowels(text: str) -> int:
    """Count ASCII vowels without regard to case."""
    return sum(char.lower() in "aeiou" for char in text)

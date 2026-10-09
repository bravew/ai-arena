def is_palindrome(text: str) -> bool:
    """Check an ASCII phrase ignoring case and non-alphanumeric characters."""
    cleaned = "".join(char.lower() for char in text if char.isalnum())
    return cleaned == cleaned[::-1]

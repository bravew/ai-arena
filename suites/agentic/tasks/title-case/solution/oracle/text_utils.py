def title_words(value: str) -> str:
    return " ".join(word.capitalize() for word in value.split())

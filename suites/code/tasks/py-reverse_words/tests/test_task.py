from solution import reverse_words


def test_contract() -> None:
    assert reverse_words(" one  two ") == "two one"
    assert reverse_words("") == ""
    assert reverse_words("solo") == "solo"

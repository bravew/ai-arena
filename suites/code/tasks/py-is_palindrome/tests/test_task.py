from solution import is_palindrome


def test_contract() -> None:
    assert is_palindrome("Never odd or even") is True
    assert is_palindrome("Arena") is False
    assert is_palindrome("") is True

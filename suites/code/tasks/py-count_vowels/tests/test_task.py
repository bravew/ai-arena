from solution import count_vowels


def test_contract() -> None:
    assert count_vowels("Arena") == 3
    assert count_vowels("rhythm") == 0
    assert count_vowels("AEIOU") == 5

from solution import gcd


def test_contract() -> None:
    assert gcd(54, 24) == 6
    assert gcd(-8, 12) == 4
    assert gcd(0, 0) == 0

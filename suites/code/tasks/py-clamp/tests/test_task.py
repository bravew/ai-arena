from solution import clamp


def test_contract() -> None:
    assert clamp(-2, 0, 5) == 0
    assert clamp(3, 0, 5) == 3
    assert clamp(9, 0, 5) == 5

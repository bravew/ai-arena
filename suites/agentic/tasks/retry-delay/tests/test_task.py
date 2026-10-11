from retry import retry_delay


def test_repository_change() -> None:
    assert [retry_delay(n) for n in (1, 2, 3, 4)] == [1, 2, 4, 8]

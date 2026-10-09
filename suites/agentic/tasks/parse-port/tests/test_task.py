from network import parse_port


def test_repository_change() -> None:
    assert parse_port("443") == 443
    for value in ("x", "0", "65536"):
        try:
            parse_port(value)
        except ValueError:
            pass
        else:
            raise AssertionError(value)

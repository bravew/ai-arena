from secrets_utils import mask_token


def test_repository_change() -> None:
    assert mask_token("abcdefgh") == "****efgh"
    assert mask_token("abc") == "***"

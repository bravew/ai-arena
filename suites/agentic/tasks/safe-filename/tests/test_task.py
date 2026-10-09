from paths import safe_filename


def test_repository_change() -> None:
    assert safe_filename("a/b/report.txt") == "report.txt"

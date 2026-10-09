import pytest
from solution import parse_port


@pytest.mark.parametrize(
    "text,expected",
    [("1", 1), ("80", 80), ("0080", 80), ("65535", 65535), ("00065535", 65535)],
)
def test_accepts_valid_ports(text: str, expected: int) -> None:
    port = parse_port(text)
    assert port == expected
    assert type(port) is int


@pytest.mark.parametrize(
    "text",
    [
        "",
        "0",
        "000",
        "65536",
        "999999",
        " 80",
        "80 ",
        "8 0",
        "\t80",
        "80\n",
        "+80",
        "-80",
        "8_0",
        "80.0",
        "0x50",
        "http",
        "\u0668\u0660",
        "\uff18\uff10",
        "\u00b2",
    ],
)
def test_rejects_invalid_ports(text: str) -> None:
    with pytest.raises(ValueError):
        parse_port(text)

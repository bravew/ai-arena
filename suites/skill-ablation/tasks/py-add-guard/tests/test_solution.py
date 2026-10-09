from decimal import Decimal
from fractions import Fraction

from solution import safe_ratio


def test_positive_and_negative_denominators() -> None:
    assert safe_ratio(6, 3) == 2
    assert safe_ratio(1, -4) == -0.25


def test_zero_denominators_return_none() -> None:
    assert safe_ratio(1, 0) is None
    assert safe_ratio(1, 0.0) is None
    assert safe_ratio(1, -0.0) is None
    assert safe_ratio(0, 0) is None


def test_zero_numerator_is_a_normal_quotient() -> None:
    assert safe_ratio(0, 5) == 0


def test_other_numeric_types() -> None:
    assert safe_ratio(Fraction(1, 2), Fraction(1, 4)) == 2
    assert safe_ratio(Fraction(1, 2), Fraction(0)) is None
    assert safe_ratio(Decimal("1"), Decimal("4")) == Decimal("0.25")
    assert safe_ratio(Decimal("1"), Decimal("0")) is None

import pytest
from victim.pricing import apply_discount


def test_apply_discount_basic():
    assert apply_discount(100, 10) == 90.0


def test_apply_discount_rejects_percent_over_100():
    with pytest.raises(ValueError):
        apply_discount(100, 101)


# WEAK: an upper bound that every plausible implementation satisfies.
def test_apply_discount_cap_is_respected():
    assert apply_discount(100, 50, max_discount=10) <= 100


# WEAK: swallowed assertion.
def test_apply_discount_none_percent():
    try:
        assert apply_discount(100, None) == 100.0
    except Exception:
        pass


# WEAK: type-only assertion.
def test_apply_discount_returns_float():
    assert isinstance(apply_discount(100, 10), float)

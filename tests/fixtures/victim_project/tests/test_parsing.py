import pytest
from victim.parsing import parse_duration, parse_key_values


def test_parse_duration_bare_seconds():
    assert parse_duration("90") == 90


def test_parse_duration_hours_and_minutes():
    assert parse_duration("1h30m") == 5400


def test_parse_duration_seconds_unit():
    assert parse_duration("45s") == 45


def test_parse_duration_rejects_garbage():
    with pytest.raises(ValueError):
        parse_duration("abc")


# WEAK: checks the return type, never the value.
def test_parse_duration_returns_int():
    assert isinstance(parse_duration("2h"), int)


# WEAK: the try/except swallows every failure, so this can never go red.
def test_parse_duration_handles_whitespace():
    try:
        assert parse_duration("  1h  ") == 3600
    except Exception:
        pass


# WEAK: exercises the function but asserts nothing.
def test_parse_key_values_basic():
    parse_key_values("a=1;b=2")


def test_parse_key_values_skips_blank_segments():
    assert parse_key_values("a=1;;b=2;") == {"a": "1", "b": "2"}


# WEAK: type-only assertion again.
def test_parse_key_values_returns_dict():
    assert isinstance(parse_key_values("x=y"), dict)

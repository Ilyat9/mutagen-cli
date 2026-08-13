import pytest
from victim.text import normalize_whitespace, truncate


def test_normalize_collapses_runs():
    assert normalize_whitespace("a   b\t\nc") == "a b c"


def test_normalize_none_becomes_empty():
    assert normalize_whitespace(None) == ""


def test_truncate_negative_limit_raises():
    with pytest.raises(ValueError):
        truncate("hello", -1)


# WEAK: truthiness only.
def test_truncate_short_text_is_returned():
    assert truncate("hi", 10)


# WEAK: bounds the length but says nothing about the content, so a
# too-aggressive cut still passes.
def test_truncate_respects_limit():
    assert len(truncate("abcdefghijklmnop", 10)) <= 10


# WEAK: substring check that any suffix-appending implementation satisfies.
def test_truncate_adds_suffix():
    assert "..." in truncate("abcdefghijklmnop", 10)

"""Small parsing helpers."""

_UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    """Parse a compact duration like ``"1h30m"`` into a number of seconds.

    A bare number is interpreted as seconds. Raises ``ValueError`` for input
    that contains no recognisable duration.
    """
    if text is None:
        raise ValueError("duration must not be None")
    text = text.strip().lower()
    if not text:
        raise ValueError("duration must not be empty")

    if text.isdigit():
        return int(text)

    total = 0
    number = ""
    matched = False
    for char in text:
        if char.isdigit():
            number += char
        elif char in _UNIT_SECONDS:
            if not number:
                raise ValueError("unit without a number: %r" % text)
            total += int(number) * _UNIT_SECONDS[char]
            number = ""
            matched = True
        else:
            raise ValueError("unexpected character %r in %r" % (char, text))

    if number:
        raise ValueError("trailing number without a unit: %r" % text)
    if not matched:
        raise ValueError("no duration found in %r" % text)
    return total


def parse_key_values(text, separator=";"):
    """Parse ``"a=1;b=2"`` into ``{"a": "1", "b": "2"}``.

    Blank segments are skipped, keys and values are stripped, values may
    themselves contain ``=`` (only the first one splits), and a later
    occurrence of a key overrides an earlier one.
    """
    if not text:
        return {}

    result = {}
    for segment in text.split(separator):
        segment = segment.strip()
        if not segment:
            continue
        if "=" not in segment:
            raise ValueError("segment without '=': %r" % segment)
        key, value = segment.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("segment with an empty key: %r" % segment)
        result[key] = value.strip()
    return result

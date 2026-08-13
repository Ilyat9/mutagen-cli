"""Text tidying helpers."""


def normalize_whitespace(text):
    """Collapse every run of whitespace into a single space and strip.

    ``None`` normalises to the empty string rather than raising.
    """
    if text is None:
        return ""
    return " ".join(text.split())


def truncate(text, limit, suffix="..."):
    """Shorten ``text`` to at most ``limit`` characters, suffix included."""
    if limit < 0:
        raise ValueError("limit must be >= 0")
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return suffix[:limit]
    return text[: limit - len(suffix)] + suffix

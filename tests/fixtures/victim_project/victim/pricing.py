"""Discount arithmetic."""


def apply_discount(price, percent, max_discount=None):
    """Apply a percentage discount, optionally capped at ``max_discount``.

    ``percent`` of ``None`` means "no discount". The result is rounded to two
    decimal places and never goes below zero.
    """
    if price is None:
        raise ValueError("price must not be None")
    if price < 0:
        raise ValueError("price must be >= 0")
    if percent is None:
        percent = 0
    if percent < 0 or percent > 100:
        raise ValueError("percent must be between 0 and 100")

    discount = price * percent / 100.0
    if max_discount is not None:
        discount = min(discount, max_discount)

    return round(price - discount, 2)

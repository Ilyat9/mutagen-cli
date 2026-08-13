"""Offset pagination with 1-based page numbers."""

from dataclasses import dataclass
from typing import Any, List


@dataclass
class Page:
    items: List[Any]
    page: int
    per_page: int
    total: int

    @property
    def has_next(self):
        return self.page < page_count(self.total, self.per_page)

    @property
    def has_prev(self):
        return self.page > 1


def page_count(total, per_page):
    """Number of pages needed to hold ``total`` items."""
    if per_page < 1:
        raise ValueError("per_page must be >= 1")
    if total <= 0:
        return 0
    return (total + per_page - 1) // per_page


def paginate(items, page=1, per_page=10):
    """Return the ``page``-th slice of ``items`` (pages are 1-based)."""
    if items is None:
        items = []
    if page < 1:
        raise ValueError("page must be >= 1")
    if per_page < 1:
        raise ValueError("per_page must be >= 1")

    start = (page - 1) * per_page
    end = start + per_page
    return Page(
        items=list(items[start:end]),
        page=page,
        per_page=per_page,
        total=len(items),
    )

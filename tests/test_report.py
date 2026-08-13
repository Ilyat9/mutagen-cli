"""Markdown report formatting."""

from mutagen_cli.models import Usage
from mutagen_cli.provider import PRICES_DATE
from mutagen_cli.report import render_markdown, summarize


def test_markdown_cost_line_names_the_price_table_date():
    # A bare dollar figure hides that the built-in price table is a snapshot
    # in time and goes stale — the date makes that visible in the report.
    summary = summarize([], duration=1.0)
    usage = Usage(calls=1, cost_usd=0.01)
    md = render_markdown([], summary, usage, "claude-opus-5", "coverage")
    assert f"prices as of {PRICES_DATE}" in md


def test_markdown_cost_line_omits_the_date_when_cost_is_unavailable():
    summary = summarize([], duration=1.0)
    usage = Usage(calls=1, unpriced_calls=1)
    md = render_markdown([], summary, usage, "unknown-model", "coverage")
    assert "cost unavailable" in md
    assert "prices as of" not in md

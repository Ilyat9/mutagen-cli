"""Markdown report formatting."""

from mutagen_cli.models import SURVIVED, Mutant, Result, Target, Usage
from mutagen_cli.provider import PRICES_DATE
from mutagen_cli.report import render_markdown, sanitize_llm_text, summarize


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


def test_sanitize_removes_image_beacon_syntax():
    text = "This is a bug ![x](https://evil.com/beacon) in your code"
    result = sanitize_llm_text(text)
    assert "evil.com" not in result
    assert "This is a bug" in result
    assert "in your code" in result


def test_sanitize_removes_html_tags():
    text = "Bug found <script>alert('xss')</script> here"
    result = sanitize_llm_text(text)
    assert "<script>" not in result
    assert "Bug found" in result
    assert "here" in result

    text2 = "Also <img src=http://evil> this"
    result2 = sanitize_llm_text(text2)
    assert "<img" not in result2
    assert "Also" in result2


def test_sanitize_preserves_normal_markdown():
    text = "This is **bold** and `inline_code` and [link](./relative.md)"
    result = sanitize_llm_text(text)
    assert "**bold**" in result
    assert "`inline_code`" in result
    assert "[link](./relative.md)" in result


def test_sanitize_escapes_bare_urls():
    text = "See https://example.com for more info"
    result = sanitize_llm_text(text)
    assert "`https://example.com`" in result
    assert "for more info" in result


def test_sanitize_leaves_inline_code_spans_untouched():
    # A description that legitimately mentions `<script>` or a URL *as code*
    # (e.g. quoting the buggy line) must survive inside its backticks — only
    # prose outside code spans is a rendering hazard.
    text = "The bug is that `<script>` is not escaped, see https://a.b for context"
    result = sanitize_llm_text(text)
    assert "`<script>`" in result
    assert "`https://a.b`" in result  # bare URL outside the span is still wrapped


def _survivor(description: str, invented_test: str) -> Result:
    target = Target(
        path="pkg/mod.py", qualname="f", start_line=1, end_line=5, source="def f(): pass"
    )
    mutant = Mutant(target=target, description=description, bug_category="other",
                     search_block="pass", replace_block="return None")
    return Result(mutant=mutant, verdict=SURVIVED, invented_test=invented_test,
                  invented_test_status="verified")


def test_render_markdown_sanitizes_description_but_not_invented_test():
    # The description is prose rendered outside a code fence — a real
    # injection surface. The invented test is Python source inside a fenced
    # code block and must reach the report byte-for-byte: mangling `<img`
    # inside a regex literal or wrapping a URL in backticks would silently
    # corrupt code a user might copy straight into their test suite.
    malicious_description = "cache leaks stale values ![x](http://evil.com/beacon)"
    test_code = (
        "def test_regex_matches_img_tag():\n"
        "    assert re.match(r'<img src=x>', text)\n"
        "    resp = requests.get(\"https://api.example.com/data\")\n"
    )
    result = _survivor(malicious_description, test_code)
    summary = summarize([result], duration=1.0)
    usage = Usage(calls=1, cost_usd=0.01)

    md = render_markdown([result], summary, usage, "claude-opus-5", "coverage")

    assert "evil.com" not in md
    assert "cache leaks stale values" in md
    assert test_code in md  # untouched, byte-for-byte, inside its ```python fence

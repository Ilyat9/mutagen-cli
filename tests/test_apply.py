from mutagen_cli.apply import apply_search_replace, make_diff

SOURCE = '''\
def paginate(items, page=1, per_page=10):
    """Return a slice."""
    if page < 1:
        raise ValueError("page must be >= 1")
    start = (page - 1) * per_page
    return items[start : start + per_page]
'''


def test_exact_match():
    result = apply_search_replace(
        SOURCE, "    start = (page - 1) * per_page", "    start = page * per_page"
    )
    assert result.ok
    assert result.method == "exact"
    assert "start = page * per_page" in result.text


def test_trailing_whitespace_is_tolerated():
    result = apply_search_replace(
        SOURCE, "    start = (page - 1) * per_page   \n", "    start = page * per_page"
    )
    assert result.ok
    assert "start = page * per_page" in result.text


def test_match_is_line_aligned_not_substring():
    # `x = 1` must not match inside `max_x = 10`.
    source = "def f():\n    max_x = 10\n    return max_x\n"
    result = apply_search_replace(source, "x = 10", "x = 99")
    assert not result.ok or "max_x = 10" not in result.text or result.method != "exact"


def test_wrong_indentation_is_repaired():
    result = apply_search_replace(
        SOURCE, "start = (page - 1) * per_page", "start = page * per_page"
    )
    assert result.ok
    assert result.method == "reindented"
    assert "\n    start = page * per_page" in result.text


def test_crlf_source_is_normalized():
    result = apply_search_replace(
        SOURCE.replace("\n", "\r\n"),
        "    if page < 1:",
        "    if page < 0:",
    )
    assert result.ok
    assert "if page < 0:" in result.text


def test_fuzzy_match_within_threshold():
    result = apply_search_replace(
        SOURCE,
        '        raise ValueError("page must be >= 1!")',
        '        raise ValueError("nope")',
    )
    assert result.ok
    assert result.method.startswith("fuzzy")


def test_unrelated_block_is_rejected():
    result = apply_search_replace(SOURCE, "    return frobnicate(widget, 42)", "    return None")
    assert not result.ok
    assert "not found" in result.reason


def test_noop_mutation_is_rejected():
    result = apply_search_replace(SOURCE, "    if page < 1:", "    if page < 1:")
    assert not result.ok
    assert result.reason == "mutation is a no-op"


def test_syntax_breaking_mutation_is_rejected():
    # Would otherwise fail every test and be miscounted as a kill.
    result = apply_search_replace(SOURCE, "    if page < 1:", "    if page < 1")
    assert not result.ok
    assert "does not parse" in result.reason


# --- the edit must land in the function we asked about ------------------------

TWINS = '''\
def alpha(items, n):
    if n < 1:
        return []
    return items[:n]


def beta(items, n):
    if n < 1:
        return []
    return items[n:]
'''
BETA_SPAN = (7, 10)
ALPHA_SPAN = (1, 4)


def test_duplicate_block_is_applied_inside_the_target_span():
    # The model was shown `beta`; the same two lines also open `alpha`. Without
    # a span the first hit wins and `alpha` is mutated instead — then we run
    # `beta`'s tests against an untouched `beta` and report a false survivor.
    result = apply_search_replace(
        TWINS,
        "    if n < 1:\n        return []",
        "    if n < 0:\n        return []",
        span=BETA_SPAN,
    )
    assert result.ok
    lines = result.text.split("\n")
    assert lines[1] == "    if n < 1:", "alpha must be untouched"
    assert lines[7] == "    if n < 0:", "beta must carry the mutation"


def test_span_restriction_works_for_the_other_twin_too():
    result = apply_search_replace(
        TWINS,
        "    if n < 1:\n        return []",
        "    if n < 0:\n        return []",
        span=ALPHA_SPAN,
    )
    assert result.ok
    lines = result.text.split("\n")
    assert lines[1] == "    if n < 0:"
    assert lines[7] == "    if n < 1:"


def test_block_outside_the_span_is_unapplicable_not_misplaced():
    # `alpha`'s body is not a licence to mutate `alpha` when we were asked
    # about `beta`, and no line of `beta` is a close enough fuzzy stand-in.
    result = apply_search_replace(
        TWINS,
        '    """alpha only"""\n    frobnicate(widget, 42)',
        "    pass",
        span=BETA_SPAN,
    )
    assert not result.ok
    assert "inside the target function" in result.reason


def test_no_match_ever_escapes_the_span():
    # Whatever strategy fires — exact, reindent, or fuzzy — the edited lines
    # must lie inside the span. `alpha` is byte-identical afterwards.
    alpha_before = "\n".join(TWINS.split("\n")[:4])
    for search in (
        "    if n < 1:\n        return []",   # exact, duplicated
        "if n < 1:\n    return []",           # reindented, duplicated
        "    return items[:n]",               # fuzzy
        "    return items[:n]   ",            # fuzzy, trailing space
    ):
        result = apply_search_replace(TWINS, search, "    return []", span=BETA_SPAN)
        if result.ok:
            assert "\n".join(result.text.split("\n")[:4]) == alpha_before, search


def test_diff_is_produced():
    result = apply_search_replace(SOURCE, "    if page < 1:", "    if page < 2:")
    diff = make_diff("x.py", SOURCE, result.text)
    assert "-    if page < 1:" in diff
    assert "+    if page < 2:" in diff

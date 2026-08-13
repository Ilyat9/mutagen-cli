"""Applying a SEARCH/REPLACE block to a file.

Exact match first, then progressively looser matching. A block we cannot place
confidently is reported as `unapplicable` rather than guessed at — a wrong
placement would produce a mutant that says nothing about the tests.

Matching is confined to the target function's own line span when the caller
knows it (`span=`). The model only ever sees one function, so a block that
matches somewhere else in the file is a coincidence, not the intended edit —
and acting on it silently mutates a function whose tests we are not running.
"""

from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass
from typing import Optional

DEFAULT_THRESHOLD = 0.85


@dataclass
class ApplyResult:
    ok: bool
    text: Optional[str] = None
    method: str = ""
    reason: str = ""
    # 1-based inclusive range of the ORIGINAL lines the replacement covered.
    # Coverage lookups use this: the tests that matter are the ones that
    # execute the lines the mutation actually changed.
    start_line: int = 0
    end_line: int = 0


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _strip_blank_edges(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _common_indent(lines: list[str]) -> str:
    indents = [
        line[: len(line) - len(line.lstrip())] for line in lines if line.strip()
    ]
    if not indents:
        return ""
    return min(indents, key=len)


def _reindent(replace_lines: list[str], from_indent: str, to_indent: str) -> list[str]:
    out = []
    for line in replace_lines:
        if not line.strip():
            out.append("")
            continue
        body = line[len(from_indent):] if line.startswith(from_indent) else line.lstrip()
        out.append(to_indent + body)
    return out


def _starts(n_lines: int, window: int, span: Optional[tuple[int, int]]) -> range:
    """Candidate start offsets for a window, confined to `span` if given.

    `span` is a 1-based inclusive line range; the window must fit inside it.
    """
    if span is None:
        return range(0, max(0, n_lines - window + 1))
    lo = max(0, span[0] - 1)
    hi = min(n_lines, span[1])  # exclusive offset of the last span line
    return range(lo, max(lo, hi - window + 1))


def apply_search_replace(
    source: str,
    search: str,
    replace: str,
    threshold: float = DEFAULT_THRESHOLD,
    span: Optional[tuple[int, int]] = None,
) -> ApplyResult:
    source = _normalize(source)
    search_lines = _strip_blank_edges(_normalize(search).split("\n"))
    replace_lines = _normalize(replace).split("\n")
    while replace_lines and not replace_lines[-1].strip():
        replace_lines.pop()

    if not search_lines:
        return ApplyResult(False, reason="empty search block")

    src_lines = source.split("\n")
    window = len(search_lines)
    search_indent = _common_indent(search_lines)

    # 1. Exact, but line-aligned. A raw substring search would happily match
    # `x = 1` inside `max_x = 10` and corrupt the file.
    hits = [
        start
        for start in _starts(len(src_lines), window, span)
        if src_lines[start : start + window] == search_lines
    ]
    if hits:
        start = hits[0]
        method = "exact" if len(hits) == 1 else f"exact ({len(hits)} matches, used first)"
        return _finish(
            source,
            "\n".join(src_lines[:start] + replace_lines + src_lines[start + window:]),
            method,
            start + 1,
            start + window,
        )

    search_stripped = [line.strip() for line in search_lines]
    search_rstripped = [line.rstrip() for line in search_lines]

    # 2. Trailing-whitespace-insensitive line match.
    for start in _starts(len(src_lines), window, span):
        chunk = src_lines[start : start + window]
        if [line.rstrip() for line in chunk] == search_rstripped:
            target_indent = _common_indent(chunk)
            new_lines = _reindent(replace_lines, search_indent, target_indent)
            return _finish(
                source,
                "\n".join(src_lines[:start] + new_lines + src_lines[start + window:]),
                "whitespace-normalized",
                start + 1,
                start + window,
            )

    # 3. Same code, different indentation (a very common model slip).
    for start in _starts(len(src_lines), window, span):
        chunk = src_lines[start : start + window]
        if [line.strip() for line in chunk] == search_stripped:
            target_indent = _common_indent(chunk)
            new_lines = _reindent(replace_lines, search_indent, target_indent)
            return _finish(
                source,
                "\n".join(src_lines[:start] + new_lines + src_lines[start + window:]),
                "reindented",
                start + 1,
                start + window,
            )

    # 4. Fuzzy: best-scoring window, if it clears the threshold.
    best_ratio, best_start, best_len = 0.0, -1, window
    for length in {max(1, window - 1), window, window + 1}:
        for start in _starts(len(src_lines), length, span):
            chunk = "\n".join(line.rstrip() for line in src_lines[start : start + length])
            ratio = difflib.SequenceMatcher(None, chunk, "\n".join(search_rstripped)).ratio()
            if ratio > best_ratio:
                best_ratio, best_start, best_len = ratio, start, length

    if best_start >= 0 and best_ratio >= threshold:
        chunk = src_lines[best_start : best_start + best_len]
        target_indent = _common_indent(chunk)
        new_lines = _reindent(replace_lines, search_indent, target_indent)
        return _finish(
            source,
            "\n".join(src_lines[:best_start] + new_lines + src_lines[best_start + best_len:]),
            f"fuzzy ({best_ratio:.2f})",
            best_start + 1,
            best_start + best_len,
        )

    where = " inside the target function" if span else ""
    return ApplyResult(
        False,
        reason=(
            f"search block not found{where} "
            f"(best fuzzy match {best_ratio:.2f} < {threshold:.2f})"
        ),
    )


def _finish(
    source: str, new_text: str, method: str, start_line: int, end_line: int
) -> ApplyResult:
    if new_text == source:
        return ApplyResult(False, method=method, reason="mutation is a no-op")
    try:
        ast.parse(new_text)
    except SyntaxError as exc:
        # A mutant that does not parse makes every test error out, which would
        # otherwise be scored as a kill. Exclude it instead of lying.
        return ApplyResult(
            False, method=method, reason=f"result does not parse: line {exc.lineno}"
        )
    return ApplyResult(
        True, text=new_text, method=method, start_line=start_line, end_line=end_line
    )


def make_diff(path: str, before: str, after: str, context: int = 2) -> str:
    diff = difflib.unified_diff(
        _normalize(before).split("\n"),
        _normalize(after).split("\n"),
        fromfile=path,
        tofile=f"{path} (mutated)",
        lineterm="",
        n=context,
    )
    return "\n".join(diff)

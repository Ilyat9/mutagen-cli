"""Reporting. The survivors are the product — everything else is context."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .models import ERROR, KILLED, SURVIVED, TIMEOUT, UNAPPLICABLE, Result, Usage
from .provider import PRICES_DATE


@dataclass
class Summary:
    counts: Counter = field(default_factory=Counter)
    by_file: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    duration: float = 0.0
    # A subset of `counts[SURVIVED]`, not a verdict of its own: the score keeps
    # exactly the shape it had before coverage mapping existed.
    no_coverage: int = 0

    @property
    def scored(self) -> int:
        return self.counts[KILLED] + self.counts[SURVIVED]

    @property
    def score(self) -> Optional[float]:
        return (self.counts[KILLED] / self.scored) if self.scored else None

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def unapplicable_rate(self) -> float:
        return (self.counts[UNAPPLICABLE] / self.total) if self.total else 0.0


def summarize(results: list[Result], duration: float = 0.0) -> Summary:
    summary = Summary(duration=duration)
    for result in results:
        summary.counts[result.verdict] += 1
        summary.by_file[result.mutant.target.path][result.verdict] += 1
        if result.no_coverage:
            summary.no_coverage += 1
    return summary


def _score_style(score: Optional[float]) -> str:
    if score is None:
        return "dim"
    if score >= 0.8:
        return "bold green"
    if score >= 0.5:
        return "bold yellow"
    return "bold red"


def _fmt_score(score: Optional[float]) -> str:
    return "n/a" if score is None else f"{score * 100:.0f}%"


def render_terminal(
    console: Console,
    results: list[Result],
    summary: Summary,
    usage: Usage,
    model: str,
    mapping: str = "heuristic",
) -> None:
    survivors = [r for r in results if r.verdict == SURVIVED and not r.no_coverage]
    unreached = [r for r in results if r.no_coverage]

    headline = Text()
    headline.append("mutation score  ", style="bold")
    headline.append(_fmt_score(summary.score), style=_score_style(summary.score))
    headline.append(
        f"   ({summary.counts[KILLED]} killed / {summary.scored} viable mutants)",
        style="dim",
    )
    console.print()
    console.print(Panel(headline, border_style=_score_style(summary.score), expand=False))

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("file")
    table.add_column("killed", justify="right")
    table.add_column("survived", justify="right", style="red")
    table.add_column("score", justify="right")
    for path in sorted(summary.by_file):
        counts = summary.by_file[path]
        scored = counts[KILLED] + counts[SURVIVED]
        score = counts[KILLED] / scored if scored else None
        table.add_row(
            path,
            str(counts[KILLED]),
            str(counts[SURVIVED]) if counts[SURVIVED] else "-",
            Text(_fmt_score(score), style=_score_style(score)),
        )
    console.print()
    console.print(table)

    if survivors:
        console.print()
        console.print(
            Text(
                f"{len(survivors)} bug{'s' if len(survivors) != 1 else ''} "
                "your tests would not catch",
                style="bold red",
            )
        )
        for index, result in enumerate(survivors, 1):
            _render_survivor(console, index, result)
    elif not unreached:
        console.print()
        console.print(
            Text("No survivors. Every mutant was caught.", style="bold green")
        )

    if unreached:
        console.print()
        console.print(
            Text(
                f"{len(unreached)} mutant(s) in code no test executes at all",
                style="bold magenta",
            )
        )
        console.print(
            Text(
                "Not a weak assertion — an absence. No test reaches these lines, "
                "so nothing here could ever have failed.",
                style="dim",
            )
        )
        for index, result in enumerate(unreached, 1):
            _render_survivor(console, index, result, style="magenta", label="unreached")

    _render_footer(console, summary, usage, model, mapping)


def _render_survivor(
    console: Console,
    index: int,
    result: Result,
    style: str = "red",
    label: str = "survivor",
) -> None:
    mutant = result.mutant
    body = Text()
    body.append(mutant.description or "(no description)", style="bold")
    body.append(f"\n{mutant.target.label}", style="dim")
    body.append(f"   [{mutant.bug_category}]", style="cyan")

    console.print()
    console.print(
        Panel(
            body,
            title=f"[{style}]{label} {index}[/{style}]",
            border_style=style,
            expand=False,
        )
    )
    if result.diff:
        console.print(Syntax(result.diff, "diff", theme="ansi_dark", background_color="default"))
    if result.invented_test:
        label = {
            "verified": "[green]suggested test — passes on your code, fails on the mutant[/green]",
            "rejected": "[yellow]suggested test — does not pass on your code "
                        "as written, needs editing[/yellow]",
            "weak": "[yellow]suggested test — passes on your code but does not "
                    "catch this mutant[/yellow]",
        }.get(result.invented_test_status, "suggested test")
        console.print(label)
        console.print(
            Syntax(result.invented_test, "python", theme="ansi_dark", background_color="default")
        )


def _fmt_cost(usage: Usage, model: str) -> str:
    if usage.unpriced_calls:
        return f"cost unavailable (no price known for {model})"
    return f"~${usage.cost_usd:.3f}"


def _fmt_cost_markdown(usage: Usage, model: str) -> str:
    """Like `_fmt_cost`, but names the price table's as-of date — the built-in
    prices go stale, and a bare dollar figure hides that."""
    if usage.unpriced_calls:
        return _fmt_cost(usage, model)
    return f"{_fmt_cost(usage, model)} (prices as of {PRICES_DATE})"


def _render_footer(
    console: Console, summary: Summary, usage: Usage, model: str, mapping: str
) -> None:
    # `unreached` is a subset of `survived`, so it is shown in parentheses
    # rather than as another term that would look additive.
    survived = f"{summary.counts[SURVIVED]} survived"
    if summary.no_coverage:
        survived += f" ({summary.no_coverage} of them unreached by any test)"
    bits = [f"{summary.counts[KILLED]} killed", survived]
    for verdict, label in ((TIMEOUT, "timeout"), (ERROR, "error"), (UNAPPLICABLE, "unapplicable")):
        if summary.counts[verdict]:
            bits.append(f"{summary.counts[verdict]} {label}")
    console.print()
    console.print(Text("  ".join(bits), style="dim"))

    cached = f", {usage.cached_calls} from cache" if usage.cached_calls else ""
    failed = f", {usage.failed_calls} failed" if usage.failed_calls else ""
    console.print(
        Text(
            f"{usage.calls} LLM call{'s' if usage.calls != 1 else ''}{cached}{failed} "
            f"({model})  {_fmt_cost(usage, model)}  in {summary.duration:.1f}s",
            style="dim",
        )
    )
    if usage.failed_calls:
        console.print(
            Text(
                f"note: {usage.failed_calls} request(s) came back unusable. Their "
                "tokens are not in the total above, but your provider may have "
                "billed them.",
                style="yellow",
            )
        )

    if mapping != "coverage":
        console.print(
            Text(
                "mapping: heuristic (install pytest-cov for precise coverage "
                "mapping)",
                style="yellow",
            )
        )

    if summary.unapplicable_rate > 0.15:
        console.print(
            Text(
                f"note: {summary.unapplicable_rate * 100:.0f}% of mutants could not be "
                "applied and were excluded from the score.",
                style="yellow",
            )
        )


# --- exports ------------------------------------------------------------------


def _markdown_mutant(out: list[str], index: int, result: Result) -> None:
    mutant = result.mutant
    out.append(f"### {index}. {mutant.description or mutant.id}\n")
    out.append(f"`{mutant.target.label}` — _{mutant.bug_category}_\n")
    if result.diff:
        out.append("```diff")
        out.append(result.diff)
        out.append("```\n")
    if result.invented_test:
        status = {
            "verified": "Verified: passes on the current code, fails on the mutant.",
            "rejected": "Not verified: fails on the current code as "
                        "written; needs editing.",
            "weak": "Not verified: passes on the current code but does "
                    "not catch this mutant.",
        }.get(result.invented_test_status, "")
        out.append("**A test that would catch it**" + (f" — {status}" if status else ""))
        out.append("")
        out.append("```python")
        out.append(result.invented_test.strip())
        out.append("```\n")


def render_markdown(
    results: list[Result],
    summary: Summary,
    usage: Usage,
    model: str,
    mapping: str = "heuristic",
) -> str:
    survivors = [r for r in results if r.verdict == SURVIVED and not r.no_coverage]
    unreached = [r for r in results if r.no_coverage]
    out: list[str] = []
    out.append("# Mutation report\n")
    out.append(
        f"**Mutation score: {_fmt_score(summary.score)}** "
        f"({summary.counts[KILLED]} killed / {summary.scored} viable mutants)\n"
    )
    out.append("| file | killed | survived | score |")
    out.append("| --- | ---: | ---: | ---: |")
    for path in sorted(summary.by_file):
        counts = summary.by_file[path]
        scored = counts[KILLED] + counts[SURVIVED]
        score = counts[KILLED] / scored if scored else None
        out.append(
            f"| `{path}` | {counts[KILLED]} | {counts[SURVIVED]} | {_fmt_score(score)} |"
        )
    out.append("")

    if survivors:
        out.append(f"## {len(survivors)} bugs your tests would not catch\n")
        for index, result in enumerate(survivors, 1):
            _markdown_mutant(out, index, result)
    elif not unreached:
        out.append("## No survivors\n")
        out.append("Every mutant was caught by the existing tests.\n")

    if unreached:
        out.append(f"## {len(unreached)} mutants in code no test executes\n")
        out.append(
            "No test reaches these lines, so nothing here could ever have "
            "failed. This is an absence of coverage, not a weak assertion.\n"
        )
        for index, result in enumerate(unreached, 1):
            _markdown_mutant(out, index, result)

    tallies = ", ".join(
        f"{summary.counts[v]} {label}"
        for v, label in (
            (KILLED, "killed"), (SURVIVED, "survived"), (TIMEOUT, "timeout"),
            (ERROR, "error"), (UNAPPLICABLE, "unapplicable"),
        )
        if summary.counts[v]
    )
    if summary.no_coverage:
        tallies += f" ({summary.no_coverage} of the survivors unreached by any test)"
    failed = f", {usage.failed_calls} failed" if usage.failed_calls else ""
    note = (
        "" if mapping == "coverage"
        else " Test mapping: heuristic — install pytest-cov for coverage-based mapping."
    )
    out.append("---\n")
    out.append(
        f"{tallies}. {usage.calls} LLM calls ({usage.cached_calls} cached{failed}) with "
        f"`{model}`, {_fmt_cost_markdown(usage, model)}, {summary.duration:.1f}s.{note}\n"
    )
    return "\n".join(out)


def render_json(
    results: list[Result],
    summary: Summary,
    usage: Usage,
    model: str,
    mapping: str = "heuristic",
) -> str:
    payload = {
        "model": model,
        "score": summary.score,
        "counts": dict(summary.counts),
        "test_mapping": mapping,
        "no_coverage": summary.no_coverage,
        "duration_seconds": round(summary.duration, 2),
        "usage": {
            "calls": usage.calls,
            "cached_calls": usage.cached_calls,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": round(usage.cost_usd, 4),
            "unpriced_calls": usage.unpriced_calls,
            "failed_calls": usage.failed_calls,
        },
        "mutants": [
            {
                "id": r.mutant.id,
                "verdict": r.verdict,
                "file": r.mutant.target.path,
                "function": r.mutant.target.qualname,
                "description": r.mutant.description,
                "bug_category": r.mutant.bug_category,
                "detail": r.detail,
                "diff": r.diff,
                "duration_seconds": round(r.duration, 3),
                "suggested_test": r.invented_test,
                "suggested_test_status": r.invented_test_status,
                "no_coverage": r.no_coverage,
            }
            for r in results
        ],
    }
    return json.dumps(payload, indent=2)


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

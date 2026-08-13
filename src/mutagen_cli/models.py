"""Core data types shared by every stage of the pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

# Verdicts. `unapplicable` is deliberately *not* a kill — counting a mutant we
# failed to apply as "caught by the tests" would inflate the score.
KILLED = "killed"
SURVIVED = "survived"
TIMEOUT = "timeout"
ERROR = "error"
UNAPPLICABLE = "unapplicable"


@dataclass
class Target:
    """A single function or method we are going to mutate."""

    path: str  # repo-relative
    qualname: str
    start_line: int
    end_line: int
    source: str
    # Test files, used for the prompt context and the --dry-run plan.
    test_files: list[str] = field(default_factory=list)
    # What to hand pytest. Node ids when the coverage map supplied them, the
    # same file list as `test_files` under the heuristic, empty for "run
    # everything".
    test_selection: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.path}::{self.qualname}"

    @property
    def selection(self) -> list[str]:
        return self.test_selection or self.test_files


@dataclass
class Mutant:
    target: Target
    description: str
    bug_category: str
    search_block: str
    replace_block: str
    index: int = 0

    @property
    def id(self) -> str:
        digest = hashlib.sha1(
            (self.target.label + self.search_block + self.replace_block).encode()
        ).hexdigest()[:8]
        return f"{self.target.qualname}#{self.index}-{digest}"


@dataclass
class Result:
    mutant: Mutant
    verdict: str
    detail: str = ""
    diff: str = ""
    duration: float = 0.0
    invented_test: Optional[str] = None
    invented_test_status: str = ""  # "verified" | "rejected" | ""
    # The coverage map says nothing executes the mutated lines. Still a
    # `survived` verdict — the score does not change shape — but it is a
    # stronger finding than a survivor that ran: no test could ever have
    # caught it, because no test reaches the code.
    no_coverage: bool = False


@dataclass
class Usage:
    """Rolling tally of what the run cost."""

    calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    # Calls whose model has no known price. When this is non-zero the report
    # says "cost unavailable" instead of quoting a made-up total.
    unpriced_calls: int = 0
    # Requests that came back unusable (transport error, unparseable reply).
    # They produced no mutants and no tokens we can see, but the provider may
    # well have billed them, so the report must not pretend they never happened.
    failed_calls: int = 0

    def add(self, other: "Usage") -> None:
        self.calls += other.calls
        self.cached_calls += other.cached_calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd
        self.unpriced_calls += other.unpriced_calls
        self.failed_calls += other.failed_calls

"""Turning targets into mutants via the LLM."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .models import Mutant, Target, Usage
from .prompts import MUTANT_SCHEMA, MUTANT_SYSTEM, mutant_user
from .provider import ProviderError

MAX_TEST_CHARS = 8000


def read_test_context(root: Path, target: Target) -> str:
    """Concatenate the test files we believe cover this target."""
    chunks = []
    budget = MAX_TEST_CHARS
    for rel in target.test_files:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if len(text) > budget:
            text = text[:budget] + "\n# ... truncated ...\n"
        chunks.append(f"# --- {rel} ---\n{text}")
        budget -= len(text)
        if budget <= 0:
            break
    return "\n\n".join(chunks)


def mutants_per_target(n_targets: int, max_mutants: int) -> int:
    if n_targets <= 0:
        return 0
    return max(3, min(8, max_mutants // n_targets or 3))


def estimate(targets: list[Target], max_mutants: int) -> tuple[int, int]:
    """(llm calls, upper bound on mutants) for the pre-run summary.

    Generation stops as soon as `max_mutants` is reached, so on a large `--all`
    the call count is bounded by the mutant budget, not by the target count.
    Quoting one call per function there overstates the bill by ~10x.
    """
    per = mutants_per_target(len(targets), max_mutants)
    if per <= 0:
        return 0, 0
    calls = min(len(targets), -(-max_mutants // per))  # ceil
    return calls, min(max_mutants, len(targets) * per)


def generate(
    root: Path,
    targets: list[Target],
    provider,
    model: str,
    max_mutants: int = 25,
    on_progress: Optional[Callable[[Target, int], None]] = None,
) -> tuple[list[Mutant], Usage, list[str]]:
    """One call per function.

    Deliberately not batched across functions: a per-function prompt carries
    that function's own tests (which is what makes the mutants aimed rather
    than generic), and a per-function cache key means editing one function
    does not invalidate the cached mutants for every other one.
    """
    per_target = mutants_per_target(len(targets), max_mutants)
    mutants: list[Mutant] = []
    usage = Usage()
    warnings: list[str] = []

    for target in targets:
        if len(mutants) >= max_mutants:
            break
        tests_source = read_test_context(root, target)
        try:
            reply = provider.complete_json(
                MUTANT_SYSTEM,
                mutant_user(target, tests_source, per_target),
                MUTANT_SCHEMA,
            )
        except ProviderError as exc:
            usage.failed_calls += 1
            warnings.append(f"{target.label}: {exc}")
            continue

        usage.calls += 1
        if reply.from_cache:
            usage.cached_calls += 1
        else:
            usage.input_tokens += reply.input_tokens
            usage.output_tokens += reply.output_tokens
            if reply.cost_usd is None:
                usage.unpriced_calls += 1
            else:
                usage.cost_usd += reply.cost_usd

        produced = 0
        seen: set[tuple[str, str, str]] = set()
        for index, raw in enumerate(reply.data.get("mutants", [])):
            if len(mutants) >= max_mutants:
                break
            search = raw.get("search_block", "")
            replace = raw.get("replace_block", "")
            if not search.strip() or search == replace:
                continue
            dedupe_key = (target.label, search, replace)
            if dedupe_key in seen:
                warnings.append(
                    f"{target.label}: dropped a duplicate mutant (same search/replace)"
                )
                continue
            seen.add(dedupe_key)
            mutants.append(
                Mutant(
                    target=target,
                    description=raw.get("description", "").strip(),
                    bug_category=raw.get("bug_category", "other"),
                    search_block=search,
                    replace_block=replace,
                    index=index + 1,
                )
            )
            produced += 1

        if on_progress:
            on_progress(target, produced)

    return mutants, usage, warnings

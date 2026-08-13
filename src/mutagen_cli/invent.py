"""`--invent`: for each surviving mutant, write the test that would catch it.

Every suggestion is checked twice before it reaches the report — it must pass
on the real code and fail on the mutant. A suggestion that fails either check
is labelled as such rather than quietly presented as a fix.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

from .apply import apply_search_replace
from .generator import read_test_context
from .models import SURVIVED, Result, Usage
from .prompts import INVENT_SCHEMA, INVENT_SYSTEM, invent_user
from .provider import ProviderError
from .runner import EXIT_OK, RunnerConfig, run_pytest

VERIFIED = "verified"
REJECTED = "rejected"      # fails on clean code — the suggestion is wrong
WEAK = "weak"              # passes on clean code but does not catch the mutant
GENERATED_DIR = "tests/mutagen_generated"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")[:40] or "mutant"


def _strip_fences(code: str) -> str:
    code = code.strip()
    if code.startswith("```"):
        lines = code.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        code = "\n".join(lines)
    return code.strip() + "\n"


def invent(
    root: Path,
    results: list[Result],
    provider,
    model: str,
    cfg: RunnerConfig,
    workdir: Path,
    apply_to_disk: bool = False,
    on_progress: Optional[Callable[[Result], None]] = None,
) -> tuple[Usage, list[str]]:
    usage = Usage()
    warnings: list[str] = []
    survivors = [r for r in results if r.verdict == SURVIVED]
    written: list[Path] = []

    for result in survivors:
        mutant = result.mutant
        try:
            reply = provider.complete_json(
                INVENT_SYSTEM,
                invent_user(mutant.target, mutant, read_test_context(root, mutant.target)),
                INVENT_SCHEMA,
            )
        except ProviderError as exc:
            usage.failed_calls += 1
            warnings.append(f"{mutant.id}: {exc}")
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

        code = _strip_fences(reply.data.get("test_code", ""))
        if not code.strip():
            continue

        rel = f"{GENERATED_DIR}/test_{_slug(mutant.target.qualname)}_{mutant.index}.py"
        status = _verify(cfg, workdir, mutant, rel, code)

        result.invented_test = code
        result.invented_test_status = status
        if on_progress:
            on_progress(result)

        if apply_to_disk and status == VERIFIED:
            destination = root / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(code, encoding="utf-8")
            written.append(destination)

    if written:
        warnings.append(
            f"wrote {len(written)} verified test file(s) to {GENERATED_DIR}/"
        )
    return usage, warnings


def _verify(
    cfg: RunnerConfig, workdir: Path, mutant, rel: str, code: str
) -> str:
    """Pass on the real code, fail on the mutant — or it is not a fix."""
    test_path = workdir / rel
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(code, encoding="utf-8")

    source_path = workdir / mutant.target.path
    try:
        clean_code, _, _ = run_pytest(
            workdir, cfg.python, [rel], cfg.timeout, ["-x", *cfg.pytest_args]
        )
        if clean_code != EXIT_OK:
            return REJECTED

        original = source_path.read_text(encoding="utf-8")
        applied = apply_search_replace(
            original,
            mutant.search_block,
            mutant.replace_block,
            span=(mutant.target.start_line, mutant.target.end_line),
        )
        if not applied.ok:
            return WEAK
        source_path.write_text(applied.text, encoding="utf-8")
        try:
            mutated_code, _, _ = run_pytest(
                workdir, cfg.python, [rel], cfg.timeout, ["-x", *cfg.pytest_args]
            )
        finally:
            source_path.write_text(original, encoding="utf-8")
        return VERIFIED if mutated_code != EXIT_OK else WEAK
    finally:
        try:
            test_path.unlink()
        except OSError:
            pass

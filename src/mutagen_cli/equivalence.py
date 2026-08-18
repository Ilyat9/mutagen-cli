"""`--classify-survivors`: ask the model whether a survivor is an equivalent mutant.

A survivor the tests legitimately missed is a finding; a survivor that no
input could ever distinguish from the original is noise. This pass puts that
distinction on the report. It is an annotation, not a verdict: the mutation
score keeps exactly the shape it had, the same convention as `no_coverage`.

The judge answers a deliberately strict question — "is there NO reachable
input for which observable behaviour differs?" — so its false positives and
false negatives can be measured against hand-labelled survivors. That
calibration lives in BENCHMARKS.md (Run I); trust the numbers there, not the
prompt's confidence.
"""

from __future__ import annotations

from typing import Callable, Optional

from .models import SURVIVED, Result, Usage
from .prompts import JUDGE_SCHEMA, JUDGE_SYSTEM, judge_user
from .provider import ProviderError


def confusion(predicted: list[bool], actual: list[bool]) -> dict:
    """Binary confusion counts plus precision/recall/F1 for the True class.

    Lives here (not in scripts/) so the calibration numbers in BENCHMARKS.md
    are computed by code the test suite covers.
    """
    if len(predicted) != len(actual):
        raise ValueError("predicted and actual must have the same length")
    tp = sum(p and a for p, a in zip(predicted, actual))
    fp = sum(p and not a for p, a in zip(predicted, actual))
    fn = sum(not p and a for p, a in zip(predicted, actual))
    tn = sum(not p and not a for p, a in zip(predicted, actual))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision and recall
        else None
    )
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1}


def classify_survivors(
    results: list[Result],
    provider,
    on_progress: Optional[Callable[[Result], None]] = None,
) -> tuple[Usage, list[str]]:
    usage = Usage()
    warnings: list[str] = []
    # Unreached survivors are a different finding — "no test runs this" — and
    # judging their equivalence would answer a question nobody asked.
    survivors = [r for r in results if r.verdict == SURVIVED and not r.no_coverage]

    for result in survivors:
        mutant = result.mutant
        try:
            reply = provider.complete_json(
                JUDGE_SYSTEM, judge_user(mutant.target, mutant), JUDGE_SCHEMA
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

        result.equivalent = bool(reply.data.get("equivalent"))
        result.equivalence_reason = str(reply.data.get("reason", ""))
        if on_progress:
            on_progress(result)

    return usage, warnings

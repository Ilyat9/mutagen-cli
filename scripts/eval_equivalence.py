#!/usr/bin/env python
"""Calibrate the equivalent-mutant judge against the hand-labelled gold set.

Gold set: benchmarks/data/equivalence_gold.json — every survivor from Runs B
and D, labelled by hand (see BENCHMARKS.md). For each one this script rebuilds
the exact judge prompt (function source from the frozen polygon, mutation
blocks recovered from the report diff) and gets a verdict, then reports the
confusion matrix, precision, recall and F1 against the hand labels.

Modes:

    --dump-prompts DIR   write one prompt file per survivor, for offline
                         answering (the Run B pattern: a model that is not
                         reachable over the API answers them into a replies
                         file keyed by mutant id)
    --replies FILE       read verdicts from such a file (offline, $0)
    --live               call the real provider (needs an API key)

Usage:
    python scripts/eval_equivalence.py --dump-prompts judge_prompts/
    python scripts/eval_equivalence.py --replies benchmarks/data/judge_replies.json
    python scripts/eval_equivalence.py --live --provider openrouter
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from mutagen_cli.cache import Cache  # noqa: E402
from mutagen_cli.equivalence import confusion  # noqa: E402
from mutagen_cli.prompts import JUDGE_SCHEMA, JUDGE_SYSTEM, judge_user  # noqa: E402
from mutagen_cli.provider import (  # noqa: E402
    ENV_KEYS,
    AnthropicProvider,
    OpenRouterProvider,
    default_model,
)

FIXTURE = REPO / "tests" / "fixtures" / "victim_project"
GOLD = REPO / "benchmarks" / "data" / "equivalence_gold.json"
REPORTS = ["live_report", "live_sonnet5", "live_opus5"]
DEFAULT_MAX_TOKENS = 16000  # part of the provider cache key, see provider.py


def function_source(path: str, qualname: str) -> str:
    src = (FIXTURE / path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    nodes = tree.body
    node = None
    for part in qualname.split("."):
        node = next(n for n in nodes if getattr(n, "name", None) == part)
        nodes = getattr(node, "body", [])
    return "\n".join(src.splitlines()[node.lineno - 1:node.end_lineno])


def blocks_from_diff(diff: str) -> tuple[str, str]:
    """Recover (search_block, replace_block) from the report's unified diff.

    Mutants are single-block edits, so the diff has one hunk; removed lines
    are the search block, added lines the replacement, indentation intact.
    A pure insertion has no removed lines: anchor on the context line right
    before it, so the pair still applies cleanly to the original source.
    """
    body = [
        line for line in diff.splitlines()
        if line and not line.startswith(("---", "+++", "@@"))
    ]
    search = [line[1:] for line in body if line.startswith("-")]
    replace = [line[1:] for line in body if line.startswith("+")]
    if not search:
        for index, line in enumerate(body):
            if line.startswith("+"):
                anchor = body[index - 1][1:]  # context line, strip the space
                search = [anchor]
                replace = [anchor, *replace]
                break
    return "\n".join(search), "\n".join(replace)


def load_cases() -> list[dict]:
    gold = json.loads(GOLD.read_text(encoding="utf-8"))["survivors"]
    reports = {
        name: json.loads((REPO / "benchmarks" / "data" / f"{name}.json").read_text())
        for name in REPORTS
    }
    cases = []
    for entry in gold:
        mutant = next(m for m in reports[entry["report"]]["mutants"] if m["id"] == entry["id"])
        search, replace = blocks_from_diff(mutant["diff"])
        target = SimpleNamespace(
            path=entry["file"],
            qualname=entry["function"],
            source=function_source(entry["file"], entry["function"]),
        )
        fake_mutant = SimpleNamespace(
            description=mutant["description"] or "",
            bug_category=mutant["bug_category"],
            search_block=search,
            replace_block=replace,
        )
        # Sanity: the recovered search block must sit inside the function.
        assert target.source.count(search) == 1, f"{entry['id']}: bad diff recovery"
        cases.append({
            "id": entry["id"],
            "report": entry["report"],
            "equivalent": entry["equivalent"],
            "junk": entry["junk"],
            "system": JUDGE_SYSTEM,
            "user": judge_user(target, fake_mutant),
        })
    return cases


def dump_prompts(cases: list[dict], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for index, case in enumerate(cases, 1):
        name = f"{index:03d}-{case['id'].replace('/', '_')}.md"
        (out / name).write_text(
            f"<!-- id: {case['id']} -->\n\n## SYSTEM\n\n{case['system']}\n\n"
            f"## USER\n\n{case['user']}\n",
            encoding="utf-8",
        )
        manifest.append({"file": name, "id": case["id"]})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "schema.json").write_text(json.dumps(JUDGE_SCHEMA, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} judge prompts to {out}/")
    print('answer them all into one replies JSON: {"<id>": {"equivalent": bool, '
          '"reason": str}, ...}')


def verdicts_live(cases: list[dict], provider: str, model: str | None, effort: str):
    model = model or default_model(provider)
    api_key = os.environ.get(ENV_KEYS[provider])
    if not api_key:
        raise SystemExit(f"--live needs {ENV_KEYS[provider]} in the environment")
    cache = Cache(REPO, enabled=True)
    llm = (
        OpenRouterProvider(model=model, api_key=api_key, cache=cache)
        if provider == "openrouter"
        else AnthropicProvider(model=model, api_key=api_key, cache=cache, effort=effort)
    )
    out = {}
    for index, case in enumerate(cases, 1):
        reply = llm.complete_json(case["system"], case["user"], JUDGE_SCHEMA)
        out[case["id"]] = reply.data
        print(f"  [{index}/{len(cases)}] {case['id']}: "
              f"{'equivalent' if reply.data.get('equivalent') else 'real'}",
              flush=True)
    return out


def report_metrics(cases: list[dict], verdicts: dict[str, dict]) -> None:
    missing = [c["id"] for c in cases if c["id"] not in verdicts]
    if missing:
        raise SystemExit(f"replies are missing {len(missing)} id(s), first: {missing[0]}")

    predicted = [bool(verdicts[c["id"]].get("equivalent")) for c in cases]
    actual_eq = [c["equivalent"] for c in cases]
    actual_junk = [c["junk"] for c in cases]

    m = confusion(predicted, actual_eq)
    print("=" * 68)
    print(f"gold set            {len(cases)} survivors "
          f"({sum(actual_eq)} equivalent, {sum(actual_junk)} junk), hand-labelled")
    print(f"judge said equivalent: {sum(predicted)}")
    print("equivalence axis (strict question, strict labels)")
    print(f"  tp {m['tp']}  fp {m['fp']}  fn {m['fn']}  tn {m['tn']}")
    for key in ("precision", "recall", "f1"):
        value = m[key]
        print(f"  {key:<10} {'n/a' if value is None else f'{value * 100:.1f}%'}")
    # The judge answers the strict question; the junk axis is the weaker
    # criterion. Report the overlap rather than pretend the judge was asked
    # about junk: of the hand-labelled junk, how many did the judge catch?
    caught = sum(p and j for p, j in zip(predicted, actual_junk))
    print(f"junk axis (weaker criterion, informational only): "
          f"{caught}/{sum(actual_junk)} hand-labelled junk flagged equivalent")
    print("disagreements:")
    for case, pred in zip(cases, predicted):
        if pred != case["equivalent"]:
            kind = "FP" if pred else "FN"
            print(f"  {kind} {case['report']}: {case['id']} — "
                  f"{verdicts[case['id']].get('reason', '')[:100]}")
    print("=" * 68)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump-prompts", default=None, metavar="DIR")
    parser.add_argument("--replies", default=None, metavar="FILE",
                        help="Verdicts keyed by mutant id, from an offline answering pass.")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--provider", choices=["openrouter", "anthropic"], default="openrouter")
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default="medium")
    args = parser.parse_args()

    cases = load_cases()
    if args.dump_prompts:
        dump_prompts(cases, Path(args.dump_prompts))
        return
    if args.replies:
        verdicts = json.loads(Path(args.replies).read_text(encoding="utf-8"))
    elif args.live:
        verdicts = verdicts_live(cases, args.provider, args.model, args.effort)
    else:
        raise SystemExit("one of --dump-prompts, --replies or --live is required")
    report_metrics(cases, verdicts)


if __name__ == "__main__":
    main()

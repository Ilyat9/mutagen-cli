#!/usr/bin/env python
"""Reproducible benchmark of mutagen against the polygon in tests/fixtures.

Two modes:

  offline (the default, always)
      Pre-seeds the on-disk LLM cache with the fixed mutant set in
      tests/fixtures/canned_mutants.json, so the run makes zero API calls and
      is byte-for-byte reproducible. This measures the ENGINE — apply, sandbox,
      execute, verdict — against a golden standard of expected verdicts.
      It says nothing about model quality.

  live (only with an explicit --live)
      Real mutant generation. This is the number that belongs in BENCHMARKS.md
      under "model quality".

Usage:
    python scripts/benchmark.py [--live] [--invent] [--provider NAME] [--model ID]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from mutagen_cli import coverage_map  # noqa: E402
from mutagen_cli.cache import Cache  # noqa: E402
from mutagen_cli.generator import mutants_per_target, read_test_context  # noqa: E402
from mutagen_cli.prompts import MUTANT_SCHEMA, MUTANT_SYSTEM, mutant_user  # noqa: E402
from mutagen_cli.provider import ENV_KEYS, default_model, reasoning_tag  # noqa: E402
from mutagen_cli.runner import (  # noqa: E402
    RunnerConfig,
    collect_coverage_map,
    prepare_workspace,
)
from mutagen_cli.scope import collect_targets, map_tests  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "victim_project"
CANNED = REPO / "tests" / "fixtures" / "canned_mutants.json"
MAX_MUTANTS = 40
MAX_FILES = 20
# Must match the `max_tokens` default in AnthropicProvider/OpenRouterProvider
# (provider.py) — it's part of the cache key.
DEFAULT_MAX_TOKENS = 16000


def git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def make_repo(dest: Path) -> Path:
    repo = dest / "victim"
    shutil.copytree(FIXTURE, repo)
    git(["init", "-q", "-b", "main"], repo)
    git(["config", "user.email", "bench@example.com"], repo)
    git(["config", "user.name", "bench"], repo)
    git(["add", "-A"], repo)
    git(["commit", "-q", "-m", "polygon"], repo)
    return repo


def _normalize(entry) -> list[dict]:
    """Accept both the canned shape (a list) and the reply shape ({"mutants": [...]})."""
    if isinstance(entry, dict):
        entry = entry.get("mutants", [])
    return [m for m in entry or [] if isinstance(m, dict)]


def _map_coverage(repo: Path, targets) -> bool:
    """Refine `targets` with a coverage map, exactly as `mutagen run` does."""
    python = sys.executable
    if not coverage_map.available(python):
        return False
    tmp = Path(tempfile.mkdtemp(prefix="mutagen-bench-cov-"))
    try:
        workdir = tmp / "w0"
        prepare_workspace(repo, workdir)
        cfg = RunnerConfig(root=repo, python=python, timeout=60.0, workers=1)
        baseline, cmap = collect_coverage_map(cfg, workdir)
        if not cmap:
            return False
        coverage_map.map_targets(cmap, targets)
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def seed_cache(
    repo: Path, provider: str, model: str, effort: str, max_mutants: int,
    source: Path = CANNED,
) -> dict[str, str]:
    """Write pre-computed replies into the cache so the run needs no API key."""
    canned = json.loads(source.read_text())
    expected: dict[str, str] = {}

    targets = collect_targets(repo, all_files=True, max_files=MAX_FILES)
    map_tests(repo, targets)
    # The prompt carries the tests that cover the function, so the cache key
    # depends on the mapping. Mirror exactly what the CLI will do, or every
    # key whose mapping differs misses and the "offline" run tries to call out.
    _map_coverage(repo, targets)
    per_target = mutants_per_target(len(targets), max_mutants)
    cache = Cache(repo, enabled=True)
    schema = json.dumps(MUTANT_SCHEMA, sort_keys=True)

    for target in targets:
        raw = _normalize(canned.get(target.label))
        for index, mutant in enumerate(raw, 1):
            expected[f"{target.qualname}#{index}"] = mutant.get("expected", "")
        payload = {
            "mutants": [
                {k: v for k, v in m.items() if k != "expected"} for m in raw
            ]
        }
        user = mutant_user(target, read_test_context(repo, target), per_target)
        # Must mirror the provider's cache key exactly, including max_tokens
        # and the sort_keys=True schema serialization (see AnthropicProvider
        # / OpenRouterProvider.complete_json in provider.py).
        if provider == "openrouter":
            key = Cache.key(
                "openrouter", model, reasoning_tag(False), str(DEFAULT_MAX_TOKENS),
                MUTANT_SYSTEM, user, schema,
            )
        else:
            key = Cache.key(
                "anthropic", model, effort, str(DEFAULT_MAX_TOKENS),
                MUTANT_SYSTEM, user, schema,
            )
        cache.put(key, {"data": payload})

    return expected


def run_mutagen(
    repo: Path, provider: str, model: str, effort: str, invent: bool, max_mutants: int
) -> tuple[dict, float, str]:
    out = repo / "bench.json"
    cmd = [
        sys.executable, "-m", "mutagen_cli.cli", "run", "--all",
        "--max-mutants", str(max_mutants), "--max-files", str(MAX_FILES),
        "--provider", provider, "--model", model, "--effort", effort,
        "--report-json", str(out), "--report-md", str(repo / "bench.md"),
    ]
    if invent:
        cmd.append("--invent")
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(repo), env=env, capture_output=True, text=True)
    elapsed = time.monotonic() - started
    if not out.exists():
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"mutagen exited {proc.returncode} without a report")
    return json.loads(out.read_text()), elapsed, proc.stdout


def bootstrap_ci(values: list[float], resamples: int = 10000, seed: int = 20260815):
    """Percentile bootstrap 95% CI of the mean. Seeded, so the printed
    interval reproduces exactly."""
    rng = random.Random(seed)
    n = len(values)
    means = sorted(
        sum(rng.choice(values) for _ in range(n)) / n for _ in range(resamples)
    )
    return means[int(0.025 * resamples)], means[int(0.975 * resamples)]


def one_run(args, source: Path | None) -> tuple[dict, float, str, dict[str, str]]:
    """Full benchmark cycle in a fresh temp repo: cold cache in live mode."""
    tmp = Path(tempfile.mkdtemp(prefix="mutagen-bench-"))
    try:
        repo = make_repo(tmp)
        expected = (
            {}
            if args.live
            else seed_cache(repo, args.provider, args.model, args.effort,
                            args.max_mutants, source or CANNED)
        )
        report, elapsed, stdout = run_mutagen(
            repo, args.provider, args.model, args.effort, args.invent, args.max_mutants
        )
        return report, elapsed, stdout, expected
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def golden_agreement(report: dict, expected: dict[str, str]) -> tuple[int, int, int]:
    agree = disagree = unknown = 0
    for mutant in report["mutants"]:
        want = expected.get(mutant["id"].split("-")[0])
        if not want:
            unknown += 1
        elif want == mutant["verdict"]:
            agree += 1
        else:
            disagree += 1
    return agree, disagree, unknown


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Call the real API.")
    parser.add_argument("--invent", action="store_true")
    parser.add_argument("--provider", choices=["openrouter", "anthropic"], default="openrouter")
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default="medium")
    parser.add_argument(
        "--max-mutants", type=int, default=MAX_MUTANTS,
        help="Ceiling on generated mutants. Lower it for a cheap live smoke test.",
    )
    parser.add_argument(
        "--save-report", default=None,
        help="Copy the JSON report here before the temp repo is cleaned up.",
    )
    parser.add_argument(
        "--replies", nargs="+", default=None,
        help="JSON of model-written replies keyed by target label, seeded into the "
             "cache instead of the canned set. Measures real mutant quality with "
             "no API calls. With --runs N, pass N files (one per run) or one file "
             "reused for every run.",
    )
    parser.add_argument(
        "--runs", type=int, default=1,
        help="Repeat the full cycle N times in fresh temp repos and report the "
             "spread plus a bootstrap CI of the mean score.",
    )
    args = parser.parse_args()

    args.model = args.model or default_model(args.provider)
    if args.live and args.replies:
        raise SystemExit("--live and --replies are mutually exclusive")
    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")
    if args.replies and len(args.replies) not in (1, args.runs):
        raise SystemExit(
            f"--replies takes 1 file or exactly --runs files, got {len(args.replies)}"
        )
    # Spending money must be an explicit act. Inferring it from a key that
    # happens to be exported (the README tells you to export one) would bill
    # anyone who runs the "offline, zero API calls" command from the README.
    live = args.live
    if live and not os.environ.get(ENV_KEYS[args.provider]):
        raise SystemExit(f"--live needs {ENV_KEYS[args.provider]} in the environment")

    sources: list[Path | None] = (
        [Path(args.replies[i if len(args.replies) > 1 else 0]) for i in range(args.runs)]
        if args.replies
        else [None] * args.runs
    )

    runs = []
    for source in sources:
        report, elapsed, stdout, expected = one_run(args, source)
        runs.append({"report": report, "elapsed": elapsed, "stdout": stdout,
                     "expected": expected})

    if args.save_report:
        payload = [r["report"] for r in runs] if args.runs > 1 else runs[0]["report"]
        Path(args.save_report).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(runs[-1]["stdout"])
    counts = runs[-1]["report"]["counts"]
    total = sum(counts.values())
    print("=" * 68)
    if live:
        mode = "live (real API calls)"
    elif args.replies:
        mode = f"offline, model-written mutants from {Path(args.replies[-1]).name}"
    else:
        mode = "offline (canned mutants)"
    print(f"mode              {mode}")
    print(f"provider          {args.provider}")
    print(f"model             {runs[-1]['report']['model']}")
    print(f"mutants           {total}")
    for verdict in ("killed", "survived", "timeout", "error", "unapplicable"):
        value = counts.get(verdict, 0)
        share = f"{value / total * 100:5.1f}%" if total else "    -"
        print(f"  {verdict:<15} {value:>3}  {share}")
    score = runs[-1]["report"]["score"]
    print(f"mutation score    {score * 100:.1f}%" if score is not None else "mutation score    n/a")
    print(f"wall time         {runs[-1]['elapsed']:.1f}s")
    usage = runs[-1]["report"]["usage"]
    print(f"llm calls         {usage['calls']} ({usage['cached_calls']} cached)")
    print(f"tokens            {usage['input_tokens']} in / {usage['output_tokens']} out")
    if usage.get("unpriced_calls"):
        print(f"cost              unavailable (no price known for {runs[-1]['report']['model']})")
    else:
        print(f"cost              ${usage['cost_usd']:.4f}")

    expected = runs[-1]["expected"]
    if expected:
        agree, disagree, unknown = golden_agreement(runs[-1]["report"], expected)
        print(f"golden standard   {agree}/{agree + disagree} verdicts as predicted")
        if disagree:
            want_by_key = expected
            for mutant in runs[-1]["report"]["mutants"]:
                key = mutant["id"].split("-")[0]
                want = want_by_key.get(key)
                if want and want != mutant["verdict"]:
                    print(f"    {key}: expected {want}, got {mutant['verdict']}")
        if unknown:
            print(f"  ({unknown} mutants had no prediction)")

    if args.runs > 1:
        print("-" * 68)
        print(f"spread over {args.runs} runs")
        print(f"  {'run':>3}  {'mutants':>7}  {'killed':>6}  "
              f"{'survived':>8}  {'score':>6}  {'wall':>6}")
        scores = []
        for i, run in enumerate(runs, 1):
            c = run["report"]["counts"]
            t = sum(c.values())
            s = run["report"]["score"]
            scores.append(s)
            print(f"  {i:>3}  {t:>7}  {c.get('killed', 0):>6}  "
                  f"{c.get('survived', 0):>8}  "
                  f"{(f'{s * 100:.1f}%' if s is not None else 'n/a'):>6}  "
                  f"{run['elapsed']:>5.1f}s")
        valid = [s for s in scores if s is not None]
        if valid:
            mean = statistics.fmean(valid)
            sd = statistics.stdev(valid) if len(valid) > 1 else 0.0
            lo, hi = bootstrap_ci(valid)
            print(f"  mean score      {mean * 100:.1f}% ± {sd * 100:.1f}% (sd)")
            print(f"  spread          {min(valid) * 100:.1f}%..{max(valid) * 100:.1f}%")
            print(f"  bootstrap 95% CI of the mean: [{lo * 100:.1f}%, {hi * 100:.1f}%]  "
                  f"(10k resamples, seeded)")
    print("=" * 68)


if __name__ == "__main__":
    main()

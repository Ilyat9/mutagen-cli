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
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from mutagen_cli.cache import Cache  # noqa: E402
from mutagen_cli.generator import mutants_per_target, read_test_context  # noqa: E402
from mutagen_cli.prompts import MUTANT_SCHEMA, MUTANT_SYSTEM, mutant_user  # noqa: E402
from mutagen_cli.provider import ENV_KEYS, default_model, reasoning_tag  # noqa: E402
from mutagen_cli.scope import collect_targets, map_tests  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "victim_project"
CANNED = REPO / "tests" / "fixtures" / "canned_mutants.json"
MAX_MUTANTS = 40
MAX_FILES = 20


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


def seed_cache(
    repo: Path, provider: str, model: str, effort: str, max_mutants: int,
    source: Path = CANNED,
) -> dict[str, str]:
    """Write pre-computed replies into the cache so the run needs no API key."""
    canned = json.loads(source.read_text())
    expected: dict[str, str] = {}

    targets = collect_targets(repo, all_files=True, max_files=MAX_FILES)
    map_tests(repo, targets)
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
        # Must mirror the provider's cache key exactly, provider name included.
        if provider == "openrouter":
            key = Cache.key(
                "openrouter", model, reasoning_tag(False), MUTANT_SYSTEM, user, schema
            )
        else:
            key = Cache.key("anthropic", model, effort, MUTANT_SYSTEM, user, schema)
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
        "--replies", default=None,
        help="JSON of model-written replies keyed by target label, seeded into the "
             "cache instead of the canned set. Measures real mutant quality with "
             "no API calls.",
    )
    args = parser.parse_args()

    args.model = args.model or default_model(args.provider)
    if args.live and args.replies:
        raise SystemExit("--live and --replies are mutually exclusive")
    # Spending money must be an explicit act. Inferring it from a key that
    # happens to be exported (the README tells you to export one) would bill
    # anyone who runs the "offline, zero API calls" command from the README.
    live = args.live
    if live and not os.environ.get(ENV_KEYS[args.provider]):
        raise SystemExit(f"--live needs {ENV_KEYS[args.provider]} in the environment")
    source = Path(args.replies) if args.replies else CANNED
    tmp = Path(tempfile.mkdtemp(prefix="mutagen-bench-"))
    try:
        repo = make_repo(tmp)
        expected = (
            {} if live
            else seed_cache(repo, args.provider, args.model, args.effort, args.max_mutants, source)
        )
        report, elapsed, stdout = run_mutagen(
            repo, args.provider, args.model, args.effort, args.invent, args.max_mutants
        )
        if args.save_report:
            Path(args.save_report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(stdout)
    counts = report["counts"]
    total = sum(counts.values())
    print("=" * 68)
    if live:
        mode = "live (real API calls)"
    elif args.replies:
        mode = f"offline, model-written mutants from {Path(args.replies).name}"
    else:
        mode = "offline (canned mutants)"
    print(f"mode              {mode}")
    print(f"provider          {args.provider}")
    print(f"model             {report['model']}")
    print(f"mutants           {total}")
    for verdict in ("killed", "survived", "timeout", "error", "unapplicable"):
        value = counts.get(verdict, 0)
        share = f"{value / total * 100:5.1f}%" if total else "    -"
        print(f"  {verdict:<15} {value:>3}  {share}")
    score = report["score"]
    print(f"mutation score    {score * 100:.1f}%" if score is not None else "mutation score    n/a")
    print(f"wall time         {elapsed:.1f}s")
    usage = report["usage"]
    print(f"llm calls         {usage['calls']} ({usage['cached_calls']} cached)")
    print(f"tokens            {usage['input_tokens']} in / {usage['output_tokens']} out")
    if usage.get("unpriced_calls"):
        print(f"cost              unavailable (no price known for {report['model']})")
    else:
        print(f"cost              ${usage['cost_usd']:.4f}")

    if expected:
        agree = disagree = unknown = 0
        rows = []
        for mutant in report["mutants"]:
            key = mutant["id"].split("-")[0]
            want = expected.get(key)
            got = mutant["verdict"]
            if not want:
                unknown += 1
            elif want == got:
                agree += 1
            else:
                disagree += 1
                rows.append(f"    {key}: expected {want}, got {got}")
        print(f"golden standard   {agree}/{agree + disagree} verdicts as predicted")
        for row in rows:
            print(row)
        if unknown:
            print(f"  ({unknown} mutants had no prediction)")
    print("=" * 68)


if __name__ == "__main__":
    main()

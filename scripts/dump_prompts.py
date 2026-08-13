#!/usr/bin/env python
"""Dump the exact per-function prompts mutagen would send, for offline answering.

Lets a model that is not reachable over the API (for example a Claude Code
subagent) act as the provider: it reads each prompt file, writes the JSON reply
it would have returned, and `benchmark.py --replies` feeds those answers back
through the real pipeline.

    python scripts/dump_prompts.py --out prompts/
    # ...answer each prompts/NN-*.md into a single replies.json...
    python scripts/benchmark.py --replies replies.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from mutagen_cli.generator import mutants_per_target, read_test_context  # noqa: E402
from mutagen_cli.prompts import MUTANT_SCHEMA, MUTANT_SYSTEM, mutant_user  # noqa: E402
from mutagen_cli.scope import collect_targets, map_tests  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "victim_project"
MAX_FILES = 20


def make_repo(dest: Path) -> Path:
    repo = dest / "victim"
    shutil.copytree(FIXTURE, repo)
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "bench@example.com"],
        ["config", "user.name", "bench"],
        ["add", "-A"],
        ["commit", "-q", "-m", "polygon"],
    ):
        subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)
    return repo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="prompts", help="Directory to write prompts into.")
    parser.add_argument("--max-mutants", type=int, default=40)
    args = parser.parse_args()

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    tmp = Path(tempfile.mkdtemp(prefix="mutagen-prompts-"))
    try:
        repo = make_repo(tmp)
        targets = collect_targets(repo, all_files=True, max_files=MAX_FILES)
        map_tests(repo, targets)
        per_target = mutants_per_target(len(targets), args.max_mutants)

        manifest = []
        for index, target in enumerate(targets, 1):
            user = mutant_user(target, read_test_context(repo, target), per_target)
            name = f"{index:02d}-{target.qualname.replace('.', '_')}.md"
            (out / name).write_text(
                f"<!-- label: {target.label} -->\n\n"
                f"## SYSTEM\n\n{MUTANT_SYSTEM}\n\n## USER\n\n{user}\n",
                encoding="utf-8",
            )
            manifest.append({"file": name, "label": target.label,
                             "qualname": target.qualname, "max_mutants": per_target})

        (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (out / "schema.json").write_text(json.dumps(MUTANT_SCHEMA, indent=2), encoding="utf-8")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"wrote {len(manifest)} prompts to {out}/")
    print(f"each may return up to {per_target} mutants")
    print("answer them all into one replies.json of the form:")
    print('  {"<label>": {"mutants": [{description, bug_category, '
          'search_block, replace_block}, ...]}, ...}')


if __name__ == "__main__":
    main()

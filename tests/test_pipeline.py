"""End-to-end: real mutants, real pytest runs, canned LLM replies."""

import subprocess
import sys

import pytest

from mutagen_cli.generator import generate
from mutagen_cli.invent import REJECTED, VERIFIED, WEAK, invent
from mutagen_cli.models import KILLED, SURVIVED, TIMEOUT, UNAPPLICABLE
from mutagen_cli.provider import ReplayProvider
from mutagen_cli.runner import (
    RunnerConfig,
    check_baseline,
    execute,
    prepare_workspace,
    run_pytest,
)
from mutagen_cli.scope import collect_targets, map_tests

# Bugs chosen against the documented blind spots in tests/fixtures/WEAK_TESTS.md.
CAP_BECOMES_FLOOR = {
    "description": "the discount cap is applied as a floor, overcharging capped orders",
    "bug_category": "wrong_operator",
    "search_block": "        discount = min(discount, max_discount)",
    "replace_block": "        discount = max(discount, max_discount)",
}
PAGE_OFF_BY_ONE = {
    "description": "pagination starts one page too far in, skipping the first page",
    "bug_category": "off_by_one",
    "search_block": "    start = (page - 1) * per_page",
    "replace_block": "    start = page * per_page",
}
LRU_LOSES_RECENCY = {
    "description": "a cache read no longer refreshes recency, so hot keys get evicted",
    "bug_category": "missing_invalidation",
    "search_block": "        self._data.move_to_end(key)",
    "replace_block": "        pass",
}
BROKEN_SYNTAX = {
    "description": "not real",
    "bug_category": "other",
    "search_block": "    if percent is None:",
    "replace_block": "    if percent is None",
}
INFINITE_LOOP = {
    "description": "page counting never terminates",
    "bug_category": "other",
    "search_block": "    if total <= 0:",
    "replace_block": "    while True:\n        pass\n    if total <= 0:",
}


def build(repo, plan, max_mutants=25):
    """plan: {qualname: [raw mutant, ...]} -> concrete Mutant list."""
    targets = [t for t in collect_targets(repo, all_files=True) if t.qualname in plan]
    targets.sort(key=lambda t: list(plan).index(t.qualname))
    map_tests(repo, targets)
    replies = [{"mutants": plan[t.qualname]} for t in targets]
    mutants, _, warnings = generate(
        repo, targets, ReplayProvider(replies), "test-model", max_mutants=max_mutants
    )
    assert warnings == []
    return mutants


def sandbox(repo, tmp_path, name="w0"):
    workdir = tmp_path / name
    prepare_workspace(repo, workdir)
    return workdir


def config(repo, timeout=60.0):
    return RunnerConfig(root=repo, python=sys.executable, timeout=timeout, workers=1)


def verdicts(results):
    return {r.mutant.target.qualname: r.verdict for r in results}


def test_baseline_is_green_on_the_polygon(victim_repo, tmp_path):
    mutants = build(victim_repo, {"apply_discount": [CAP_BECOMES_FLOOR]})
    baseline = check_baseline(config(victim_repo), mutants, sandbox(victim_repo, tmp_path))
    assert baseline.green, baseline.output


def test_red_suite_is_detected(victim_repo, tmp_path):
    path = victim_repo / "victim" / "pricing.py"
    path.write_text(path.read_text().replace("return round(price - discount, 2)",
                                             "return 0.0"))
    mutants = build(victim_repo, {"apply_discount": [CAP_BECOMES_FLOOR]})
    baseline = check_baseline(config(victim_repo), mutants, sandbox(victim_repo, tmp_path))
    assert not baseline.green


def test_weak_tests_let_bugs_through_and_strong_tests_do_not(victim_repo, tmp_path):
    mutants = build(victim_repo, {
        "apply_discount": [CAP_BECOMES_FLOOR],
        "paginate": [PAGE_OFF_BY_ONE],
        "LRUCache.get": [LRU_LOSES_RECENCY],
    })
    results = execute(config(victim_repo), mutants, [sandbox(victim_repo, tmp_path)])
    seen = verdicts(results)

    # The weak tests (vacuous bound; swallowed assertion) miss these.
    assert seen["apply_discount"] == SURVIVED
    assert seen["LRUCache.get"] == SURVIVED
    # The real assertion on page contents catches this one.
    assert seen["paginate"] == KILLED

    killed = next(r for r in results if r.verdict == KILLED)
    assert "test_paginate_first_page" in killed.detail
    assert "-    start = (page - 1) * per_page" in killed.diff


def test_unparseable_mutant_is_excluded_not_counted_as_a_kill(victim_repo, tmp_path):
    mutants = build(victim_repo, {"apply_discount": [BROKEN_SYNTAX]})
    results = execute(config(victim_repo), mutants, [sandbox(victim_repo, tmp_path)])
    assert results[0].verdict == UNAPPLICABLE
    assert "does not parse" in results[0].detail


def test_infinite_loop_is_a_timeout_not_a_kill(victim_repo, tmp_path):
    mutants = build(victim_repo, {"page_count": [INFINITE_LOOP]})
    results = execute(config(victim_repo, timeout=6.0), mutants, [sandbox(victim_repo, tmp_path)])
    assert results[0].verdict == TIMEOUT


SAME_SIZE_BOUNDARY = {
    "description": "page zero is accepted instead of rejected",
    "bug_category": "boundary_condition",
    "search_block": "    if page < 1:",
    "replace_block": "    if page < 0:",
}


def test_same_size_mutation_is_not_masked_by_stale_bytecode(victim_repo, tmp_path):
    # `< 1` -> `< 0` keeps the file byte-length identical, so a .pyc written in
    # the same second would shadow it and the mutant would look like a survivor.
    mutants = build(victim_repo, {"paginate": [SAME_SIZE_BOUNDARY]})
    workdir = sandbox(victim_repo, tmp_path)
    cfg = config(victim_repo)
    assert check_baseline(cfg, mutants, workdir).green  # populates any caches first
    results = execute(cfg, mutants, [workdir])
    assert results[0].verdict == KILLED


def test_editable_src_install_cannot_shadow_the_worker_copy(tmp_path, monkeypatch):
    # A src-layout project installed editable (pip install -e .) puts the
    # ORIGINAL tree on sys.path via a site-packages .pth. Without the PYTHONPATH
    # fix, tests in the worker copy import that unmutated code and every mutant
    # is a false survivor. Simulated here by a competing "installed" package on
    # PYTHONPATH: the worker copy's src must win.
    installed = tmp_path / "installed" / "pkg"
    installed.mkdir(parents=True)
    (installed / "__init__.py").write_text('VALUE = "original"\n')
    worker = tmp_path / "worker"
    (worker / "src" / "pkg").mkdir(parents=True)
    (worker / "src" / "pkg" / "__init__.py").write_text('VALUE = "mutant"\n')
    (worker / "tests").mkdir()
    (worker / "tests" / "test_pkg.py").write_text(
        "import pkg\n\n\ndef test_worker_copy_wins():\n    assert pkg.VALUE == 'mutant'\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(installed.parent))
    code, output, _ = run_pytest(worker, sys.executable, ["tests/test_pkg.py"], 30.0)
    assert code == 0, output


TWINS_MODULE = '''\
def alpha(items, n):
    if n < 1:
        return []
    return items[:n]


def beta(items, n):
    if n < 1:
        return []
    return items[n:]
'''
TWIN_MUTANT = {
    "description": "beta accepts n=0",
    "bug_category": "boundary_condition",
    "search_block": "    if n < 1:\n        return []",
    "replace_block": "    if n < 0:\n        return []",
}


def test_mutation_lands_in_the_target_function_not_a_twin(victim_repo, tmp_path):
    # Two functions in one file open with identical lines. The model was asked
    # about `beta`, so `beta` is what must change — mutating `alpha` and then
    # running `beta`'s tests is how a survivor becomes an artefact.
    (victim_repo / "victim" / "twins.py").write_text(TWINS_MODULE)
    (victim_repo / "tests" / "test_twins.py").write_text(
        "from victim.twins import alpha, beta\n\n\n"
        "def test_alpha_rejects_zero():\n    assert alpha([1, 2], 0) == []\n"
    )
    mutants = build(victim_repo, {"beta": [TWIN_MUTANT]})
    assert len(mutants) == 1
    results = execute(config(victim_repo), mutants, [sandbox(victim_repo, tmp_path)])

    assert results[0].verdict != UNAPPLICABLE, results[0].detail
    diff = results[0].diff
    # The hunk header carries the line number: beta's guard is line 8, alpha's
    # is line 2. A diff anchored near the top of the file means we hit alpha.
    assert "-    if n < 1:" in diff
    changed_line = int(diff.split("@@ -")[1].split(",")[0])
    assert changed_line > 4, f"mutation landed in alpha, not beta:\n{diff}"


DUPLICATE_LINE_MODULE = '''\
def clamp_or_zero(a, b):
    if a < 0:
        return 0
    if b < 0:
        return 0
    return a + b
'''
DUPLICATE_LINE_MUTANT = {
    "description": "negative guard returns -1 instead of 0",
    "bug_category": "wrong_operator",
    "search_block": "        return 0",
    "replace_block": "        return -1",
}


def test_multiple_exact_matches_within_span_is_flagged_in_the_report(victim_repo, tmp_path):
    # "        return 0" occurs twice inside clamp_or_zero's own span. apply.py
    # silently takes the first hit rather than refusing — the model broke its
    # own "match exactly once" rule, and the report must say so.
    (victim_repo / "victim" / "dupe.py").write_text(DUPLICATE_LINE_MODULE)
    (victim_repo / "tests" / "test_dupe.py").write_text(
        "from victim.dupe import clamp_or_zero\n\n\n"
        "def test_clamp_or_zero_adds():\n    assert clamp_or_zero(1, 2) == 3\n"
    )
    mutants = build(victim_repo, {"clamp_or_zero": [DUPLICATE_LINE_MUTANT]})
    assert len(mutants) == 1
    results = execute(config(victim_repo), mutants, [sandbox(victim_repo, tmp_path)])

    assert len(results) == 1
    assert results[0].verdict != UNAPPLICABLE, results[0].detail
    assert "2 matches" in results[0].detail
    assert "used first" in results[0].detail


def test_the_working_tree_is_never_modified(victim_repo, tmp_path):
    before = (victim_repo / "victim" / "pricing.py").read_text()
    mutants = build(victim_repo, {"apply_discount": [CAP_BECOMES_FLOOR]})
    execute(config(victim_repo), mutants, [sandbox(victim_repo, tmp_path)])
    assert (victim_repo / "victim" / "pricing.py").read_text() == before
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=victim_repo, capture_output=True, text=True
    )
    assert status.stdout.strip() == ""


def test_workspace_copy_keeps_migrations_package_data(tmp_path):
    # "migrations" is skipped for mutation scope, but a package that ships SQL
    # migrations as data breaks in the worker copy if the directory is dropped.
    repo = tmp_path / "repo"
    (repo / "src" / "pkg" / "storage" / "migrations").mkdir(parents=True)
    (repo / "src" / "pkg" / "storage" / "migrations" / "001_init.sql").write_text("select 1;")
    workdir = tmp_path / "worker"
    prepare_workspace(repo, workdir)
    assert (workdir / "src" / "pkg" / "storage" / "migrations" / "001_init.sql").exists()


def test_mutants_run_in_parallel_across_workers(victim_repo, tmp_path):
    mutants = build(victim_repo, {
        "apply_discount": [CAP_BECOMES_FLOOR],
        "paginate": [PAGE_OFF_BY_ONE],
        "LRUCache.get": [LRU_LOSES_RECENCY],
    })
    workdirs = [sandbox(victim_repo, tmp_path, f"w{i}") for i in range(3)]
    results = execute(config(victim_repo), mutants, workdirs)
    assert len(results) == 3
    assert verdicts(results)["paginate"] == KILLED


# --- invent -------------------------------------------------------------------

GOOD_TEST = {
    "test_name": "test_cap_is_a_ceiling",
    "explanation": "pins the capped value",
    "test_code": (
        "from victim.pricing import apply_discount\n\n\n"
        "def test_cap_is_a_ceiling():\n"
        "    assert apply_discount(100, 50, max_discount=10) == 90.0\n"
    ),
}
FAILS_ON_CLEAN_CODE = {
    "test_name": "test_wrong",
    "explanation": "bogus",
    "test_code": (
        "from victim.pricing import apply_discount\n\n\n"
        "def test_wrong():\n"
        "    assert apply_discount(100, 50, max_discount=10) == 1.0\n"
    ),
}
MISSES_THE_MUTANT = {
    "test_name": "test_vacuous",
    "explanation": "does not actually pin the cap",
    "test_code": (
        "from victim.pricing import apply_discount\n\n\n"
        "def test_vacuous():\n"
        "    assert apply_discount(100, 50, max_discount=10) <= 100\n"
    ),
}


def _survivor_results(victim_repo, tmp_path):
    mutants = build(victim_repo, {"apply_discount": [CAP_BECOMES_FLOOR]})
    workdir = sandbox(victim_repo, tmp_path)
    results = execute(config(victim_repo), mutants, [workdir])
    assert results[0].verdict == SURVIVED
    return results, workdir


@pytest.mark.parametrize(
    "reply,expected",
    [(GOOD_TEST, VERIFIED), (FAILS_ON_CLEAN_CODE, REJECTED), (MISSES_THE_MUTANT, WEAK)],
)
def test_invented_tests_are_verified_both_ways(victim_repo, tmp_path, reply, expected):
    results, workdir = _survivor_results(victim_repo, tmp_path)
    invent(
        victim_repo, results, ReplayProvider([reply]), "test-model",
        config(victim_repo), workdir,
    )
    assert results[0].invented_test_status == expected
    assert "def test_" in results[0].invented_test


def test_invent_apply_only_writes_verified_tests(victim_repo, tmp_path):
    results, workdir = _survivor_results(victim_repo, tmp_path)
    invent(
        victim_repo, results, ReplayProvider([FAILS_ON_CLEAN_CODE]), "test-model",
        config(victim_repo), workdir, apply_to_disk=True,
    )
    assert not (victim_repo / "tests" / "mutagen_generated").exists()

    results2, workdir2 = _survivor_results(victim_repo, tmp_path / "second")
    invent(
        victim_repo, results2, ReplayProvider([GOOD_TEST]), "test-model",
        config(victim_repo), workdir2, apply_to_disk=True,
    )
    written = list((victim_repo / "tests" / "mutagen_generated").glob("*.py"))
    assert len(written) == 1
    assert "apply_discount" in written[0].read_text()


def test_pytest_subprocess_does_not_see_mutagens_secrets(victim_repo, tmp_path, monkeypatch):
    # Test code in the mutated repo is untrusted (a pull request's own code on
    # the GitHub Action) and must not be able to read mutagen's own API keys
    # or the token used to post PR comments out of its environment.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-secret")

    workdir = sandbox(victim_repo, tmp_path)
    probe = workdir / "tests" / "test_probe_env.py"
    probe.write_text(
        "import os\n\n"
        "def test_secrets_are_not_in_the_environment():\n"
        "    for var in ('OPENROUTER_API_KEY', 'ANTHROPIC_API_KEY', 'GITHUB_TOKEN'):\n"
        "        assert var not in os.environ\n",
        encoding="utf-8",
    )
    code, output, _ = run_pytest(workdir, sys.executable, ["tests/test_probe_env.py"], 30.0)
    assert code == 0, output


def test_symlinks_are_copied_as_content_not_links(tmp_path):
    # Regression: symlinks=True in shutil.copytree allows sandbox escape via
    # symlinks pointing outside the repo. Ensure symlinks are copied as content.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "code.py").write_text("x = 1")

    # Create a file outside the repo
    outside = tmp_path / "outside_file.txt"
    outside.write_text("secret")

    # Create a symlink inside repo pointing to outside file
    (repo / "src" / "link_to_secret").symlink_to(outside)

    # Copy workspace
    workdir = tmp_path / "worker"
    prepare_workspace(repo, workdir)

    # The symlink should be copied as content (a file), not as a link
    copied_link = workdir / "src" / "link_to_secret"
    assert copied_link.exists()
    assert not copied_link.is_symlink(), "Symlink was not converted to content"
    assert copied_link.read_text() == "secret"

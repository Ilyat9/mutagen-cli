import json
import subprocess
import sys

from click.testing import CliRunner

from mutagen_cli.cache import Cache
from mutagen_cli.cli import main
from mutagen_cli.generator import mutants_per_target, read_test_context
from mutagen_cli.prompts import MUTANT_SCHEMA, MUTANT_SYSTEM, mutant_user
from mutagen_cli.provider import reasoning_tag
from mutagen_cli.scope import collect_targets, map_tests

MODEL = "anthropic/claude-sonnet-5"  # the CLI's default with --provider openrouter
EFFORT = "medium"


def seed(repo, plan, max_mutants=25, max_files=20):
    """Warm the disk cache so a CLI run needs no API key."""
    targets = collect_targets(repo, all_files=True, max_files=max_files)
    map_tests(repo, targets)
    per_target = mutants_per_target(len(targets), max_mutants)
    cache = Cache(repo, enabled=True)
    schema = json.dumps(MUTANT_SCHEMA, sort_keys=True)
    for target in targets:
        payload = {"mutants": plan.get(target.qualname, [])}
        user = mutant_user(target, read_test_context(repo, target), per_target)
        cache.put(
            Cache.key("openrouter", MODEL, reasoning_tag(False), MUTANT_SYSTEM, user, schema),
            {"data": payload},
        )


SURVIVOR = {
    "description": "the discount cap acts as a floor",
    "bug_category": "wrong_operator",
    "search_block": "        discount = min(discount, max_discount)",
    "replace_block": "        discount = max(discount, max_discount)",
}


def invoke(repo, args, monkeypatch):
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return CliRunner().invoke(main, args, catch_exceptions=False)


def test_dry_run_costs_nothing_and_shows_the_plan(victim_repo, monkeypatch):
    result = invoke(victim_repo, ["run", "--all", "--dry-run"], monkeypatch)
    assert result.exit_code == 0
    assert "LLM call" in result.output
    assert "apply_discount" in result.output
    assert "tests/test_pricing.py" in result.output


def test_no_changes_exits_cleanly(victim_repo, monkeypatch):
    result = invoke(victim_repo, ["run"], monkeypatch)
    assert result.exit_code == 0
    assert "Nothing to mutate" in result.output


def test_full_run_reports_a_survivor_and_writes_exports(victim_repo, monkeypatch, tmp_path):
    seed(victim_repo, {"apply_discount": [SURVIVOR]})
    md = tmp_path / "r.md"
    js = tmp_path / "r.json"
    result = invoke(
        victim_repo,
        ["run", "--all", "--workers", "1", "--python", sys.executable,
         "--report-md", str(md), "--report-json", str(js)],
        monkeypatch,
    )
    assert result.exit_code == 0
    assert "mutation score" in result.output
    assert "1 bug your tests would not catch" in result.output

    payload = json.loads(js.read_text())
    assert payload["counts"]["survived"] == 1
    assert payload["score"] == 0.0
    assert payload["mutants"][0]["function"] == "apply_discount"
    assert "discount cap acts as a floor" in md.read_text()


# Three bugs the polygon's *strong* tests pin exactly. Each has a named test
# that must go red. If any of these comes back "survived", the pipeline is
# reporting an artefact — the mutation never reached the code pytest imported.
DEFINITELY_KILLED = {
    "paginate": [{
        "description": "pagination skips the first page's items",
        "bug_category": "off_by_one",
        "search_block": "    start = (page - 1) * per_page",
        "replace_block": "    start = page * per_page",
    }],  # test_paginate_first_page asserts items == [1, 2, 3]
    "page_count": [{
        "description": "an empty collection reports one page instead of none",
        "bug_category": "empty_input",
        "search_block": "    if total <= 0:\n        return 0",
        "replace_block": "    if total <= 0:\n        return 1",
    }],  # test_page_count_of_empty asserts == 0
    "normalize_whitespace": [{
        "description": "None comes back as None instead of an empty string",
        "bug_category": "none_handling",
        "search_block": "    if text is None:\n        return \"\"",
        "replace_block": "    if text is None:\n        return None",
    }],  # test_normalize_none_becomes_empty asserts == ""
}


def test_known_killable_mutants_are_all_reported_killed(victim_repo, monkeypatch, tmp_path):
    seed(victim_repo, DEFINITELY_KILLED)
    js = tmp_path / "r.json"
    result = invoke(
        victim_repo,
        ["run", "--all", "--workers", "2", "--python", sys.executable,
         "--report-json", str(js)],
        monkeypatch,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(js.read_text())

    assert len(payload["mutants"]) == 3, payload["mutants"]
    for mutant in payload["mutants"]:
        assert mutant["verdict"] == "killed", (mutant["function"], mutant["detail"])
        assert mutant["detail"], "a kill must name the test that failed"
    assert payload["counts"] == {"killed": 3}
    assert payload["score"] == 1.0
    assert "No survivors" in result.output

    # Each kill names its own test, not somebody else's.
    by_function = {m["function"]: m["detail"] for m in payload["mutants"]}
    assert "test_paginate_first_page" in by_function["paginate"]
    assert "test_page_count_of_empty" in by_function["page_count"]
    assert "test_normalize_none_becomes_empty" in by_function["normalize_whitespace"]


def test_fail_under_gates_the_exit_code(victim_repo, monkeypatch):
    seed(victim_repo, {"apply_discount": [SURVIVOR]})
    result = invoke(
        victim_repo,
        ["run", "--all", "--workers", "1", "--python", sys.executable, "--fail-under", "70"],
        monkeypatch,
    )
    assert result.exit_code == 1
    assert "below the --fail-under threshold" in result.output


def test_run_outside_a_git_repo_is_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["run", "--all"], catch_exceptions=False)
    assert result.exit_code == 2
    assert "not inside a git repository" in result.output


def test_missing_api_key_says_so_instead_of_blaming_the_model(victim_repo, monkeypatch):
    # Nothing seeded, no key: every call fails for one reason. The user must be
    # told that reason, not "the model returned no usable mutants".
    result = invoke(victim_repo, ["run", "--all", "--max-mutants", "2"], monkeypatch)
    assert result.exit_code == 2
    assert "no OpenRouter API key found" in result.output
    assert "no usable mutants" not in result.output
    # ...and the same reason is not repeated once per function.
    assert result.output.count("openrouter.ai/keys") == 2  # one warning + the error


def test_path_outside_the_repo_is_an_error_not_a_traceback(victim_repo, tmp_path, monkeypatch):
    outside = tmp_path / "elsewhere.py"
    outside.write_text("def f():\n    return 1\n")
    result = invoke(victim_repo, ["run", "--path", str(outside)], monkeypatch)
    assert result.exit_code == 2
    assert "outside the repository" in result.output


def test_unknown_python_interpreter_is_an_error_not_a_traceback(victim_repo, monkeypatch):
    seed(victim_repo, {"apply_discount": [SURVIVOR]})
    result = invoke(
        victim_repo,
        ["run", "--all", "--workers", "1", "--python", "/nonexistent/python"],
        monkeypatch,
    )
    assert result.exit_code == 2
    assert "no such interpreter" in result.output


def test_missing_pytest_is_not_reported_as_a_red_suite(victim_repo, monkeypatch, tmp_path):
    # `python -m pytest` without pytest exits 1 — indistinguishable from a
    # failing suite by exit code alone.
    bare = tmp_path / "bare"
    subprocess.run([sys.executable, "-m", "venv", str(bare)], check=True, capture_output=True)
    bare_python = (
        bare / "Scripts" / "python.exe" if sys.platform == "win32" else bare / "bin" / "python"
    )
    seed(victim_repo, {"apply_discount": [SURVIVOR]})
    result = invoke(
        victim_repo,
        ["run", "--all", "--workers", "1", "--python", str(bare_python)],
        monkeypatch,
    )
    assert result.exit_code == 2
    assert "pytest is not installed" in result.output
    assert "not green" not in result.output


def test_coverage_mapping_is_announced(victim_repo, monkeypatch):
    seed(victim_repo, {"apply_discount": [SURVIVOR]})
    result = invoke(
        victim_repo,
        ["run", "--all", "--workers", "1", "--python", sys.executable],
        monkeypatch,
    )
    assert result.exit_code == 0, result.output
    assert "mapping: coverage" in result.output
    assert "install pytest-cov" not in result.output


def test_without_pytest_cov_it_falls_back_to_the_heuristic(victim_repo, monkeypatch, tmp_path):
    monkeypatch.setattr("mutagen_cli.cli.coverage_map.available", lambda python: False)
    seed(victim_repo, {"apply_discount": [SURVIVOR]})
    js = tmp_path / "r.json"
    result = invoke(
        victim_repo,
        ["run", "--all", "--workers", "1", "--python", sys.executable,
         "--report-json", str(js)],
        monkeypatch,
    )
    assert result.exit_code == 0, result.output
    assert "mapping: heuristic (install pytest-cov for precise coverage mapping)" in (
        " ".join(result.output.split())
    )
    # The run still works — the heuristic is a fallback, not an error.
    payload = json.loads(js.read_text())
    assert payload["test_mapping"] == "heuristic"
    assert payload["counts"]["survived"] == 1


def test_installed_entry_point_runs():
    proc = subprocess.run(
        [sys.executable, "-m", "mutagen_cli.cli", "--version"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert "mutagen" in proc.stdout

"""Coverage-based test mapping.

The point of the whole feature is one scenario: the test that would kill a
mutant lives in a file the filename/symbol heuristic does not pick. Under the
heuristic the mutant is a false survivor; under coverage it is killed.
"""

import subprocess
import sys
import textwrap

import pytest

from mutagen_cli import coverage_map
from mutagen_cli.generator import generate
from mutagen_cli.models import KILLED, SURVIVED
from mutagen_cli.provider import ReplayProvider
from mutagen_cli.runner import RunnerConfig, check_baseline, execute, prepare_workspace
from mutagen_cli.scope import collect_targets, map_tests

VAT_BUG = {
    "description": "VAT is charged at 30% instead of 20%",
    "bug_category": "wrong_default",
    "search_block": "    return round(amount * 1.2, 2)",
    "replace_block": "    return round(amount * 1.3, 2)",
}


def write(repo, rel, text):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


@pytest.fixture
def hidden_killer_repo(tmp_path):
    """A repo where the only killing test never names the function it kills.

    `test_totals.py` mentions neither `charge` nor `billing`, so it scores zero
    in the heuristic and never makes the top three — while three vacuous decoys
    that do name them score highly. Coverage sees straight through that.
    """
    repo = tmp_path / "repo"
    write(repo, "app/__init__.py", "")
    write(repo, "app/billing.py", '''
        def charge(amount):
            """Amount plus VAT."""
            if amount < 0:
                raise ValueError("amount must be >= 0")
            return round(amount * 1.2, 2)
    ''')
    write(repo, "app/checkout.py", """
        from .billing import charge


        def total_for(price):
            return charge(price)
    """)
    # The real coverage — names nothing the heuristic scores on.
    write(repo, "tests/test_totals.py", """
        from app.checkout import total_for


        def test_total_includes_vat():
            assert total_for(100) == 120.0
    """)
    # Decoys: high heuristic scores, no ability to kill anything.
    write(repo, "tests/test_billing.py", """
        from app.billing import charge


        def test_charge_returns_something():
            assert charge(10) is not None
    """)
    for name in ("test_alpha.py", "test_beta.py"):
        write(repo, f"tests/{name}", f"""
            from app.billing import charge


            def test_charge_is_a_number_{name[5:-3]}():
                assert isinstance(charge(5), float)
        """)

    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    return repo


def build(repo, qualname, raw):
    targets = [t for t in collect_targets(repo, all_files=True) if t.qualname == qualname]
    map_tests(repo, targets)
    mutants, _, warnings = generate(
        repo, targets, ReplayProvider([{"mutants": [raw]}]), "test-model"
    )
    assert warnings == []
    return targets, mutants


def sandbox(repo, tmp_path, name="w0"):
    workdir = tmp_path / name
    prepare_workspace(repo, workdir)
    return workdir


def cfg_for(repo):
    return RunnerConfig(root=repo, python=sys.executable, timeout=60.0, workers=1)


def collect(repo, tmp_path):
    """Run the instrumented baseline and return (workdir, map)."""
    workdir = sandbox(repo, tmp_path)
    baseline = check_baseline(cfg_for(repo), [], workdir, collect_coverage=True)
    assert baseline.green, baseline.output
    return workdir, coverage_map.load(workdir, repo)


def test_the_heuristic_misses_the_killing_test(hidden_killer_repo, tmp_path):
    """The premise. If this ever stops holding, the test below proves nothing."""
    targets, mutants = build(hidden_killer_repo, "charge", VAT_BUG)
    assert "tests/test_totals.py" not in targets[0].test_files
    results = execute(cfg_for(hidden_killer_repo), mutants, [sandbox(hidden_killer_repo, tmp_path)])
    assert results[0].verdict == SURVIVED  # a false survivor


def test_coverage_mapping_finds_the_killing_test(hidden_killer_repo, tmp_path):
    targets, mutants = build(hidden_killer_repo, "charge", VAT_BUG)
    workdir, cmap = collect(hidden_killer_repo, tmp_path)
    assert cmap, "no coverage data collected"

    refined, uncovered = coverage_map.map_targets(cmap, targets)
    assert refined == 1
    assert uncovered == 0
    assert any("test_totals.py" in nodeid for nodeid in targets[0].test_selection)

    results = execute(cfg_for(hidden_killer_repo), mutants, [workdir], coverage=cmap)
    assert results[0].verdict == KILLED, results[0].detail
    assert "test_total_includes_vat" in results[0].detail


def test_every_covering_test_is_recorded_not_just_the_first(hidden_killer_repo, tmp_path):
    """Guards `coverage_map.COVERAGE_CORE`.

    coverage's default `sysmon` core on Python 3.12+ stops emitting line events
    once a line has been seen, so only the first test to reach a line is
    recorded against it. A map like that would send us to run a subset of the
    covering tests — precisely the false-survivor bug this feature exists to
    fix, but harder to spot. `charge`'s return line is reached by four tests.
    """
    _, cmap = collect(hidden_killer_repo, tmp_path)
    tests = cmap.tests_for("app/billing.py", 1, 100)
    assert len(tests) >= 4, f"only {len(tests)} contexts recorded: {tests}"


def test_uncovered_code_is_flagged_no_coverage(hidden_killer_repo, tmp_path):
    write(hidden_killer_repo, "app/dead.py", """
        def never_called(items):
            total = 0
            for item in items:
                total += item
            return total
    """)
    targets, mutants = build(hidden_killer_repo, "never_called", {
        "description": "the running total starts at one",
        "bug_category": "off_by_one",
        "search_block": "    total = 0",
        "replace_block": "    total = 1",
    })
    workdir, cmap = collect(hidden_killer_repo, tmp_path)
    coverage_map.map_targets(cmap, targets)

    results = execute(cfg_for(hidden_killer_repo), mutants, [workdir], coverage=cmap)
    assert results[0].verdict == SURVIVED
    assert results[0].no_coverage is True
    assert results[0].detail == "no test executes the mutated lines"
    # Flagged from the map, not by running a suite that could only pass.
    assert results[0].duration == 0.0
    assert results[0].diff, "the diff is still what the user needs to see"


def test_covered_code_is_never_flagged_no_coverage(hidden_killer_repo, tmp_path):
    targets, mutants = build(hidden_killer_repo, "charge", VAT_BUG)
    workdir, cmap = collect(hidden_killer_repo, tmp_path)
    coverage_map.map_targets(cmap, targets)
    results = execute(cfg_for(hidden_killer_repo), mutants, [workdir], coverage=cmap)
    assert results[0].no_coverage is False


def test_missing_data_file_yields_an_empty_map(tmp_path):
    assert not coverage_map.load(tmp_path, tmp_path)


def test_an_empty_map_leaves_the_heuristic_mapping_alone(hidden_killer_repo):
    targets, _ = build(hidden_killer_repo, "charge", VAT_BUG)
    before = list(targets[0].test_files)
    refined, uncovered = coverage_map.map_targets(coverage_map.CoverageMap(), targets)
    assert (refined, uncovered) == (0, 0)
    assert targets[0].test_files == before


def test_a_file_the_map_never_saw_keeps_its_heuristic_mapping(hidden_killer_repo, tmp_path):
    # "Absent from the map" is ambiguous — never imported, or filtered out by
    # a source setting. Only a measured-but-uncovered file is called uncovered.
    targets, _ = build(hidden_killer_repo, "charge", VAT_BUG)
    before = list(targets[0].test_files)
    cmap = coverage_map.CoverageMap({"app/other.py": {1: {"tests/test_x.py::test_y"}}})
    coverage_map.map_targets(cmap, targets)
    assert targets[0].test_files == before


def test_huge_selections_collapse_to_files():
    many = [f"tests/test_a.py::test_{i}" for i in range(coverage_map.MAX_NODEIDS + 1)]
    assert coverage_map.selection_for(many) == ["tests/test_a.py"]
    few = ["tests/test_a.py::test_1", "tests/test_b.py::test_2"]
    assert coverage_map.selection_for(few) == few


def test_the_test_phase_suffix_is_dropped():
    # setup and teardown coverage counts as coverage; only the phase goes.
    assert coverage_map._nodeid("tests/test_x.py::test_y|setup") == "tests/test_x.py::test_y"
    assert coverage_map._nodeid("tests/test_x.py::test_y") == "tests/test_x.py::test_y"

import pytest

from mutagen_cli.generator import estimate, generate, mutants_per_target, read_test_context
from mutagen_cli.models import Target
from mutagen_cli.provider import ProviderError, ReplayProvider, price_of
from mutagen_cli.scope import collect_targets, map_tests


def reply(*mutants):
    return {"mutants": list(mutants)}


MUTANT = {
    "description": "cap becomes a floor",
    "bug_category": "wrong_operator",
    "search_block": "        discount = min(discount, max_discount)",
    "replace_block": "        discount = max(discount, max_discount)",
}


def test_per_target_count_is_clamped():
    assert mutants_per_target(1, 25) == 8
    assert mutants_per_target(25, 25) == 3
    assert mutants_per_target(0, 25) == 0


def test_estimate_respects_the_ceiling():
    targets = [Target("a.py", f"f{i}", 1, 2, "def f(): pass") for i in range(10)]
    calls, mutants = estimate(targets, max_mutants=25)
    assert calls == 9  # ceil(25 / 3 per target); generation stops at the budget
    assert mutants <= 25


def test_estimate_does_not_quote_calls_the_budget_forbids():
    # `--all` on a big repo: generation stops at max_mutants, so promising one
    # call per function overstates the bill by an order of magnitude.
    targets = [Target("a.py", f"f{i}", 1, 2, "def f(): pass") for i in range(90)]
    calls, _ = estimate(targets, max_mutants=25)
    assert calls == 9

    # A budget larger than the work still costs one call per function.
    calls, _ = estimate(targets, max_mutants=1000)
    assert calls == 90


def test_failed_calls_are_counted_not_silently_dropped(victim_repo):
    # A request that comes back unusable still cost something. Reporting only
    # the successes makes the run look cheaper than it was.
    targets = [t for t in collect_targets(victim_repo, all_files=True)
               if t.qualname in ("apply_discount", "paginate")]
    map_tests(victim_repo, targets)

    class Failing:
        def complete_json(self, system, user, schema):
            raise ProviderError("model returned unparseable JSON")

    mutants, usage, warnings = generate(victim_repo, targets, Failing(), "test-model")
    assert mutants == []
    assert usage.calls == 0
    assert usage.failed_calls == len(targets)
    assert len(warnings) == len(targets)


def test_generate_builds_mutants(victim_repo):
    targets = [t for t in collect_targets(victim_repo, all_files=True)
               if t.qualname == "apply_discount"]
    map_tests(victim_repo, targets)
    provider = ReplayProvider([reply(MUTANT)])

    mutants, usage, warnings = generate(victim_repo, targets, provider, "test-model")

    assert warnings == []
    assert usage.calls == 1
    assert len(mutants) == 1
    assert mutants[0].bug_category == "wrong_operator"
    assert mutants[0].target.qualname == "apply_discount"
    assert mutants[0].id.startswith("apply_discount#1-")


def test_generate_drops_noop_mutants(victim_repo):
    targets = [t for t in collect_targets(victim_repo, all_files=True)
               if t.qualname == "apply_discount"]
    noop = dict(MUTANT, replace_block=MUTANT["search_block"])
    empty = dict(MUTANT, search_block="   ")
    mutants, _, _ = generate(
        victim_repo, targets, ReplayProvider([reply(noop, empty, MUTANT)]), "m"
    )
    assert len(mutants) == 1


def test_generate_dedupes_identical_mutants_in_one_reply(victim_repo):
    # Two mutants with the same (target, search_block, replace_block) are the
    # same edit twice — keep the first, drop the rest.
    targets = [t for t in collect_targets(victim_repo, all_files=True)
               if t.qualname == "apply_discount"]
    duplicate = dict(MUTANT)
    other = dict(MUTANT, replace_block="        discount = 0")
    mutants, _, warnings = generate(
        victim_repo, targets, ReplayProvider([reply(MUTANT, duplicate, other)]), "m"
    )
    assert len(mutants) == 2
    assert mutants[0].replace_block == MUTANT["replace_block"]
    assert mutants[1].replace_block == "        discount = 0"
    assert any("duplicate" in w for w in warnings)


def test_generate_honours_max_mutants(victim_repo):
    targets = [t for t in collect_targets(victim_repo, all_files=True)
               if t.qualname == "apply_discount"]
    many = [dict(MUTANT, replace_block=f"        discount = {i}") for i in range(8)]
    mutants, _, _ = generate(
        victim_repo, targets, ReplayProvider([reply(*many)]), "m", max_mutants=3
    )
    assert len(mutants) == 3


def test_generate_warns_when_max_mutants_skips_whole_functions(victim_repo):
    # Two targets, a budget that's exhausted entirely by the first: the
    # second is never even called, and that should be visible, not silent.
    targets = [t for t in collect_targets(victim_repo, all_files=True)
               if t.qualname in ("apply_discount", "paginate")]
    many = [dict(MUTANT, replace_block=f"        discount = {i}") for i in range(3)]
    mutants, _, warnings = generate(
        victim_repo, targets, ReplayProvider([reply(*many)]), "m", max_mutants=3
    )
    assert len(mutants) == 3
    assert any("--max-mutants" in w and "1 function" in w for w in warnings)


def test_test_context_includes_mapped_files(victim_repo):
    targets = collect_targets(victim_repo, all_files=True)
    map_tests(victim_repo, targets)
    target = next(t for t in targets if t.qualname == "apply_discount")
    context = read_test_context(victim_repo, target)
    assert "test_apply_discount_basic" in context


def test_pricing_is_per_million_tokens():
    assert price_of("claude-opus-5", 1_000_000, 0) == pytest.approx(5.0)
    assert price_of("claude-opus-5", 0, 1_000_000) == pytest.approx(25.0)
    assert price_of("anthropic/claude-sonnet-5", 1_000_000, 0) == pytest.approx(2.0)
    assert price_of("unknown-model", 1_000_000, 1_000_000) is None


def test_pricing_overrides_beat_the_builtin_table():
    overrides = {"anthropic/claude-sonnet-5": [1.0, 4.0], "new-model": [0.5, 2.0]}
    assert price_of("anthropic/claude-sonnet-5", 1_000_000, 0, overrides) == pytest.approx(1.0)
    assert price_of("new-model", 0, 1_000_000, overrides) == pytest.approx(2.0)

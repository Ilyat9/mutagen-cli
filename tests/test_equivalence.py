"""The equivalent-mutant judge: --classify-survivors and its calibration metrics."""

import json

from mutagen_cli.equivalence import classify_survivors, confusion
from mutagen_cli.models import KILLED, SURVIVED, Mutant, Result, Target, Usage
from mutagen_cli.prompts import JUDGE_SCHEMA, JUDGE_SYSTEM, judge_user
from mutagen_cli.provider import ReplayProvider
from mutagen_cli.report import render_json, render_markdown, summarize


def _target() -> Target:
    return Target(
        path="victim/cache.py",
        qualname="LRUCache.invalidate",
        start_line=39,
        end_line=44,
        source=(
            '    def invalidate(self, key):\n'
            '        """Drop a single key. Returns True if something was removed."""\n'
            "        if key in self._data:\n"
            "            del self._data[key]\n"
            "            return True\n"
            "        return False"
        ),
    )


def _survivor(no_coverage: bool = False) -> Result:
    mutant = Mutant(
        target=_target(),
        description="Key is removed from storage but left in the LRU order",
        bug_category="state_leak",
        search_block="            del self._data[key]",
        replace_block="            self._data.pop(key)",
        index=2,
    )
    return Result(mutant=mutant, verdict=SURVIVED, no_coverage=no_coverage)


def test_judge_prompt_carries_the_source_the_blocks_and_the_strict_question():
    prompt = judge_user(_target(), _survivor().mutant)
    assert "del self._data[key]" in prompt
    assert "self._data.pop(key)" in prompt
    assert "def invalidate" in prompt
    assert "NO reachable input" in prompt


def test_judge_prompts_keep_the_injection_preamble():
    assert "not instructions" in JUDGE_SYSTEM
    assert JUDGE_SCHEMA["required"] == ["equivalent", "reason"]


def test_classify_annotates_survivors_and_skips_the_rest():
    judged = _survivor()
    unreached = _survivor(no_coverage=True)
    killed_mutant = _survivor()
    killed_mutant.verdict = KILLED
    provider = ReplayProvider([{"equivalent": True, "reason": "pop == del here"}])

    usage, warnings = classify_survivors([judged, unreached, killed_mutant], provider)

    assert judged.equivalent is True
    assert judged.equivalence_reason == "pop == del here"
    # Unreached and killed mutants are never sent to the judge.
    assert unreached.equivalent is None
    assert killed_mutant.equivalent is None
    assert provider.calls == 1
    assert usage.calls == 1 and not warnings


def test_classify_counts_a_failed_call_and_keeps_going():
    survivor = _survivor()
    provider = ReplayProvider([])  # raises ProviderError on the first call

    usage, warnings = classify_survivors([survivor], provider)

    assert survivor.equivalent is None
    assert usage.failed_calls == 1
    assert len(warnings) == 1


def test_confusion_counts_and_metrics():
    m = confusion([True, True, False, False], [True, False, True, False])
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 1)
    assert m["precision"] == 0.5
    assert m["recall"] == 0.5
    assert m["f1"] == 0.5


def test_confusion_empty_prediction_axes_are_none_not_zero():
    m = confusion([False], [False])
    assert m["precision"] is None
    assert m["recall"] is None
    assert m["f1"] is None
    assert m["tn"] == 1


def test_json_report_carries_the_annotation():
    judged = _survivor()
    judged.equivalent = True
    judged.equivalence_reason = "identical on an OrderedDict"
    untouched = _survivor()
    summary = summarize([judged, untouched], duration=1.0)
    payload = json.loads(render_json([judged, untouched], summary, Usage(), "m"))

    first, second = payload["mutants"]
    assert first["equivalent"] is True
    assert first["equivalence_reason"] == "identical on an OrderedDict"
    # None, not false: "the judge never ran" must not read as "judged real".
    assert second["equivalent"] is None
    assert second["equivalence_reason"] is None


def test_markdown_marks_likely_equivalents():
    judged = _survivor()
    judged.equivalent = True
    judged.equivalence_reason = "identical on an OrderedDict"
    summary = summarize([judged], duration=1.0)
    md = render_markdown([judged], summary, Usage(), "m")
    assert "Likely an equivalent mutant" in md
    assert "identical on an OrderedDict" in md


def test_markdown_says_nothing_when_the_judge_did_not_run():
    survivor = _survivor()
    summary = summarize([survivor], duration=1.0)
    md = render_markdown([survivor], summary, Usage(), "m")
    assert "equivalent" not in md

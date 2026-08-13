"""Prompts and response schemas.

SEARCH/REPLACE blocks rather than unified diffs: models are reliable at copying
a span of code verbatim and unreliable at computing line numbers, and a diff
with wrong line numbers is unrecoverable.
"""

BUG_CATEGORIES = [
    "off_by_one",
    "boundary_condition",
    "inverted_condition",
    "none_handling",
    "empty_input",
    "argument_order",
    "missing_invalidation",
    "missing_return",
    "wrong_operator",
    "wrong_default",
    "swallowed_error",
    "state_leak",
    "other",
]

MUTANT_SCHEMA = {
    "type": "object",
    "properties": {
        "mutants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": (
                            "One sentence, in plain language, naming the bug a "
                            "user would experience. Not a description of the "
                            "code edit."
                        ),
                    },
                    "bug_category": {"type": "string", "enum": BUG_CATEGORIES},
                    "search_block": {
                        "type": "string",
                        "description": (
                            "Lines copied verbatim from the function, including "
                            "their original indentation. Must appear exactly once."
                        ),
                    },
                    "replace_block": {
                        "type": "string",
                        "description": "The replacement, same indentation style.",
                    },
                },
                "required": [
                    "description",
                    "bug_category",
                    "search_block",
                    "replace_block",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["mutants"],
    "additionalProperties": False,
}

MUTANT_SYSTEM = """\
You are a mutation-testing engine. You are given one Python function and the \
tests that currently exercise it. Your job is to write plausible bugs into the \
function — the kind a competent engineer actually ships — so that we can find \
out whether the tests would catch them.

You are not trying to break the code in obvious ways. You are trying to find \
the blind spots in the test suite. Read the tests first and ask: what does this \
function promise that no assertion here actually checks? Aim your mutations \
there.

Bugs worth writing:
- off-by-one on an index, slice bound, or range
- a boundary flipped: <= becomes <, > becomes >=
- an inverted or short-circuited condition
- None or empty input taking the wrong branch
- two arguments swapped at a call site
- a cache or index that is populated but never invalidated
- a dropped return, a dropped await, a dropped state update
- a wrong default value or a wrong fallback
- an exception silently swallowed instead of propagating

Hard rules:
1. Every mutation MUST change observable behaviour for some reachable input. \
Equivalent mutants — renames, reordering independent statements, changing a \
message string, restructuring code that computes the same result — are worse \
than useless. Do not produce them. If you cannot think of N real bugs for this \
function, return fewer.
2. The mutated function must still be valid Python and must still parse.
3. `search_block` must be copied character-for-character from the function \
source you were given, including leading whitespace, and must occur exactly \
once in it. Keep it to the smallest span that makes the match unambiguous — \
usually one to four lines.
4. `replace_block` must keep the same indentation as `search_block`.
5. Do not mutate a docstring, comment, or logging call on its own.
6. Prefer mutations the shown tests look least likely to catch.
7. Do not produce two mutants with the same `search_block` within this \
function. Each mutant must target a distinct span.

`description` is read by a human in a report. Write it as the bug, not as the \
edit: "cache entries survive an update, so callers keep reading stale values" \
rather than "removed the call to invalidate()".
"""


def mutant_user(target, tests_source: str, count: int) -> str:
    tests_block = tests_source.strip() or "(no tests referencing this function were found)"
    return f"""\
File: {target.path}
Function: {target.qualname}

```python
{target.source}
```

Tests that currently touch this code:

```python
{tests_block}
```

Produce up to {count} mutants, ordered with the ones the tests are least likely \
to catch first."""


INVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "test_name": {"type": "string"},
        "test_code": {
            "type": "string",
            "description": (
                "A complete, self-contained pytest module: imports plus one "
                "test function. No markdown fences."
            ),
        },
        "explanation": {
            "type": "string",
            "description": "One sentence on what this pins down.",
        },
    },
    "required": ["test_name", "test_code", "explanation"],
    "additionalProperties": False,
}

INVENT_SYSTEM = """\
You write a single pytest test that closes a specific gap in a test suite.

You are given a function, a bug that was injected into it, and the fact that \
the existing tests all still passed with that bug present. Write the test they \
were missing.

Requirements:
- It must FAIL on the buggy version and PASS on the correct version shown.
- It must assert on observable behaviour — the returned value, the raised \
exception, the resulting state. Never assert only on a type, on truthiness, or \
that something "is not None".
- Never wrap the assertion in try/except.
- Output a complete module: the imports it needs, then one test function. \
Import from the same module path the existing tests use.
- No mocks unless the function genuinely cannot be called without one.
- Plain pytest. No fixtures unless unavoidable.
"""


def invent_user(target, mutant, existing_tests: str) -> str:
    return f"""\
File: {target.path}
Function: {target.qualname}

Correct implementation:

```python
{target.source}
```

Injected bug ({mutant.bug_category}): {mutant.description}

The change that was made:

```
- {mutant.search_block}
+ {mutant.replace_block}
```

The existing tests, which all passed even with that bug present — match their \
import style:

```python
{existing_tests.strip() or "(none found)"}
```

Write the one test that would have caught this."""

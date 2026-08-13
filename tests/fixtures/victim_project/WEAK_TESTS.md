# The polygon: what is deliberately weak

`victim/` is a small, **correct** library. `tests/` has 36 tests and they all
pass. Exactly half of them are deliberately worthless. This file is the golden
standard: it records which tests are fake and which behaviours therefore have no
protection, so we can check what mutagen actually finds.

Do not "fix" the weak tests. They are the fixture.

## The 18 weak tests

| Test | Why it proves nothing |
| --- | --- |
| `test_parse_duration_returns_int` | Asserts the return type, never the value. |
| `test_parse_duration_handles_whitespace` | `try/except: pass` swallows the assertion. |
| `test_parse_key_values_basic` | Calls the function and asserts nothing at all. |
| `test_parse_key_values_returns_dict` | Type-only assertion. |
| `test_paginate_second_page_has_right_length` | Checks `len(items) == 3`, never which items. |
| `test_page_count_rounds_up` | `>= 2` is satisfied by both rounding rules. |
| `test_paginate_does_not_consume_input` | Asserts on the *input* list, not the result. |
| `test_page_has_next_is_bool` | Type-only assertion on a boolean property. |
| `test_lru_ordering_refreshes_on_get` | `try/except: pass` swallows both assertions. |
| `test_invalidate_prefix_returns_a_count` | `is not None` — `0` satisfies it. |
| `test_hit_counter_is_non_negative` | `hits >= 0` is vacuously true. |
| `test_len_reflects_contents` | No assertion, pure smoke. |
| `test_truncate_short_text_is_returned` | Truthiness only. |
| `test_truncate_respects_limit` | Upper-bounds the length, says nothing about content. |
| `test_truncate_adds_suffix` | Substring check any suffix-appending code satisfies. |
| `test_apply_discount_cap_is_respected` | `<= 100` is satisfied by every plausible impl. |
| `test_apply_discount_none_percent` | `try/except: pass`. |
| `test_apply_discount_returns_float` | Type-only assertion. |

The other 18 tests are real and should catch real regressions.

## Behaviours with no protection

These are the blind spots the weak tests leave. A good mutation run should
produce a surviving mutant pointing at each one.

| # | Blind spot | Where |
| --- | --- | --- |
| B1 | LRU recency is not refreshed on read | `LRUCache.get` |
| B2 | Hit/miss counters can be wrong in any way | `LRUCache.get` |
| B3 | Eviction can fire one entry early | `LRUCache.set` |
| B4 | `invalidate_prefix` can match the wrong keys or delete nothing | `LRUCache.invalidate_prefix` |
| B5 | `capacity < 1` validation can be removed | `LRUCache.__init__` |
| B6 | `has_next` is unconstrained beyond being a bool | `Page.has_next` |
| B7 | `has_prev` is entirely unconstrained | `Page.has_prev` |
| B8 | Ceiling division can become floor division | `page_count` |
| B9 | `per_page < 1` validation is untested | `page_count`, `paginate` |
| B10 | Page ≥ 2 offsets are only length-checked | `paginate` |
| B11 | Whitespace trimming and lower-casing of input | `parse_duration` |
| B12 | Values containing `=` (only the first split is specified) | `parse_key_values` |
| B13 | Value whitespace stripping | `parse_key_values` |
| B14 | Empty-key rejection | `parse_key_values` |
| B15 | Later keys overriding earlier ones | `parse_key_values` |
| B16 | The discount cap direction (`min` vs `max`) | `apply_discount` |
| B17 | Negative-percentage rejection | `apply_discount` |
| B18 | Rounding to 2 decimal places | `apply_discount` |
| B19 | `price is None` / `price < 0` rejection | `apply_discount` |
| B20 | Truncation is one-sided: only "not longer than the limit" | `truncate` |
| B21 | Exact-length boundary (`len(text) == limit`) | `truncate` |
| B22 | The `limit <= len(suffix)` branch is never entered | `truncate` |

## Reproducing

```bash
cd tests/fixtures/victim_project && python -m pytest -q   # 36 passed
python scripts/benchmark.py                               # from the repo root
```

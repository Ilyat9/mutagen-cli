# Mutation report

**Mutation score: 75%** (18 killed / 24 viable mutants)

| file | killed | survived | score |
| --- | ---: | ---: | ---: |
| `src/parsy/__init__.py` | 18 | 6 | 75% |

## 6 bugs your tests would not catch

### 1. Swallows unrelated errors like ValueError or IndexError from line_info_at instead of letting them propagate, masking real bugs in edge cases.

`src/parsy/__init__.py::ParseError.line_info` — _swallowed_error_

```diff
--- src/parsy/__init__.py
+++ src/parsy/__init__.py (mutated)
@@ -31,5 +31,5 @@
         try:
             return "{}:{}".format(*line_info_at(self.stream, self.index))
-        except (TypeError, AttributeError):  # not a str
+        except Exception:  # not a str
             return str(self.index)
 
```

### 2. When there are zero expected tokens, the code still tries to format a single-expectation message and crashes or shows a broken message instead of a sensible fallback.

`src/parsy/__init__.py::ParseError.__str__` — _boundary_condition_

```diff
--- src/parsy/__init__.py
+++ src/parsy/__init__.py (mutated)
@@ -37,5 +37,5 @@
         expected_list = sorted(repr(e) for e in self.expected)
 
-        if len(expected_list) == 1:
+        if len(expected_list) <= 1:
             return f"expected {expected_list[0]} at {self.line_info()}"
         else:
```

### 3. A successful parse result records the wrong furthest-failure index, which can cause incorrect error reporting when combined with other parsers in alternatives.

`src/parsy/__init__.py::Result.success` — _wrong_default_

```diff
--- src/parsy/__init__.py
+++ src/parsy/__init__.py (mutated)
@@ -53,5 +53,5 @@
     @staticmethod
     def success(index, value) -> Result:
-        return Result(True, index, value, -1, frozenset())
+        return Result(True, index, value, index, frozenset())
 
     @staticmethod
```

### 4. Failure results always report success-index -1 correctly, but the expected value is wrapped in a set even when None is passed, causing merged error messages to include a spurious 'None' entry instead of being omitted

`src/parsy/__init__.py::Result.failure` — _wrong_default_

```diff
--- src/parsy/__init__.py
+++ src/parsy/__init__.py (mutated)
@@ -57,5 +57,5 @@
     @staticmethod
     def failure(index, expected) -> Result:
-        return Result(False, -1, None, index, frozenset([expected]))
+        return Result(False, -1, None, index, frozenset([expected]) if expected is not None else frozenset())
 
     # collect the furthest failure from self and other
```

### 5. Aggregating a falsy-but-present other result (e.g. one with furthest==0 or empty expected set) is incorrectly treated as if there were no other result to combine, silently dropping information about that failure.

`src/parsy/__init__.py::Result.aggregate` — _none_handling_

```diff
--- src/parsy/__init__.py
+++ src/parsy/__init__.py (mutated)
@@ -61,5 +61,5 @@
     # collect the furthest failure from self and other
     def aggregate(self, other) -> Result:
-        if not other:
+        if other is None:
             return self
 
```

### 6. Parsers built via subclassing or composition end up sharing a single mutable default wrapped function instead of the one actually passed in, causing unrelated parsers to behave identically.

`src/parsy/__init__.py::Parser.__init__` — _wrong_default_

```diff
--- src/parsy/__init__.py
+++ src/parsy/__init__.py (mutated)
@@ -95,5 +95,5 @@
         and returns a Result.
         """
-        self.wrapped_fn = wrapped_fn
+        self.wrapped_fn = wrapped_fn or string("")
 
     def __call__(self, stream: str | bytes | list, index: int) -> Any:
```

---

18 killed, 6 survived, 1 timeout. 10 LLM calls (0 cached) with `anthropic/claude-sonnet-5`, ~$0.129, 94.5s.

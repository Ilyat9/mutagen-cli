# Mutation report

**Mutation score: 68%** (17 killed / 25 viable mutants)

| file | killed | survived | score |
| --- | ---: | ---: | ---: |
| `parse/__init__.py` | 17 | 8 | 68% |

## 7 bugs your tests would not catch

### 1. Numbers with a '0x', '0o', or '0b' prefix are misdetected as decimal when the sign character shifts the prefix position, causing incorrect base selection for signed hex/octal/binary literals

`parse/__init__.py::int_convert.__call__` — _off_by_one_

```diff
--- parse/__init__.py
+++ parse/__init__.py (mutated)
@@ -86,5 +86,5 @@
 
             # For number formats starting with 0b, 0o, 0x, use corresponding base ...
-            if string[number_start] == "0" and len(string) - number_start > 2:
+            if string[number_start] == "0" and len(string) - number_start >= 2:
                 if string[number_start + 1] in "bB":
                     base = 2
```

### 2. Empty matched strings are now converted to None before reaching the user's converter, so custom converters never see empty-string input even when they should handle it themselves.

`parse/__init__.py::convert_first.__call__` — _empty_input_

```diff
--- parse/__init__.py
+++ parse/__init__.py (mutated)
@@ -108,4 +108,6 @@
 
     def __call__(self, string, match):
+        if not string:
+            return None
         return self.converter(string)
 
```

### 3. Timezone offset stored in seconds is silently treated as minutes, so any FixedTzOffset built with a non-zero offset represents the wrong UTC delta.

`parse/__init__.py::FixedTzOffset.__init__` — _wrong_operator_

```diff
--- parse/__init__.py
+++ parse/__init__.py (mutated)
@@ -121,5 +121,5 @@
 
     def __init__(self, offset, name):
-        self._offset = timedelta(minutes=offset)
+        self._offset = timedelta(minutes=offset) if offset == 0 else timedelta(seconds=offset)
         self._name = name
 
```

### 4. The offset sign gets flipped, so timezones east of UTC are recorded as west and vice versa.

`parse/__init__.py::FixedTzOffset.__init__` — _wrong_operator_

```diff
--- parse/__init__.py
+++ parse/__init__.py (mutated)
@@ -121,5 +121,5 @@
 
     def __init__(self, offset, name):
-        self._offset = timedelta(minutes=offset)
+        self._offset = timedelta(minutes=-offset)
         self._name = name
 
```

### 5. The timezone name is stored but silently lowercased/altered, so display and equality checks relying on the original name text fail.

`parse/__init__.py::FixedTzOffset.__init__` — _other_

```diff
--- parse/__init__.py
+++ parse/__init__.py (mutated)
@@ -122,5 +122,5 @@
     def __init__(self, offset, name):
         self._offset = timedelta(minutes=offset)
-        self._name = name
+        self._name = name.upper() if name else name
 
     def __repr__(self):
```

### 6. UTC-tagged datetimes end up with a timezone offset different from zero, shifting parsed times that use the 'Z' suffix by a small constant amount instead of none.

`parse/__init__.py::FixedTzOffset.utcoffset` — _wrong_operator_

```diff
--- parse/__init__.py
+++ parse/__init__.py (mutated)
@@ -128,5 +128,5 @@
 
     def utcoffset(self, dt):
-        return self._offset
+        return self._offset + timedelta(minutes=1)
 
     def tzname(self, dt):
```

### 7. Calling utcoffset always returns None regardless of the configured offset, breaking timezone-aware datetime comparisons and arithmetic.

`parse/__init__.py::FixedTzOffset.utcoffset` — _missing_return_

```diff
--- parse/__init__.py
+++ parse/__init__.py (mutated)
@@ -128,5 +128,5 @@
 
     def utcoffset(self, dt):
-        return self._offset
+        pass
 
     def tzname(self, dt):
```

## 1 mutants in code no test executes

No test reaches these lines, so nothing here could ever have failed. This is an absence of coverage, not a weak assertion.

### 1. The timezone name lookup always returns None instead of the configured name, breaking display of timezone abbreviations.

`parse/__init__.py::FixedTzOffset.tzname` — _wrong_default_

```diff
--- parse/__init__.py
+++ parse/__init__.py (mutated)
@@ -131,5 +131,5 @@
 
     def tzname(self, dt):
-        return self._name
+        return None
 
     def dst(self, dt):
```

---

17 killed, 8 survived (1 of the survivors unreached by any test). 11 LLM calls (0 cached) with `anthropic/claude-sonnet-5`, ~$0.133, 69.1s.

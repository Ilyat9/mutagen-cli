"""On-disk cache for LLM responses.

Repeat runs are the common case while iterating on a test suite, and paying the
API twice for an identical prompt is pure waste. Keyed on the full prompt so a
prompt change invalidates naturally.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

CACHE_VERSION = "1"


class Cache:
    def __init__(self, root: Path, enabled: bool = True):
        self.dir = Path(root) / ".mutagen" / "cache"
        self.enabled = enabled
        if self.enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(*parts: str) -> str:
        h = hashlib.sha256()
        h.update(CACHE_VERSION.encode())
        for part in parts:
            h.update(b"\0")
            h.update(part.encode("utf-8"))
        return h.hexdigest()

    def get(self, key: str) -> Optional[dict]:
        if not self.enabled:
            return None
        path = self.dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, key: str, value: dict) -> None:
        if not self.enabled:
            return
        path = self.dir / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value), encoding="utf-8")
        tmp.replace(path)

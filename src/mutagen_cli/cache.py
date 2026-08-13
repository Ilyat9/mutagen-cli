"""On-disk cache for LLM responses.

Repeat runs are the common case while iterating on a test suite, and paying the
API twice for an identical prompt is pure waste. Keyed on the full prompt so a
prompt change invalidates naturally.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

CACHE_VERSION = "1"

_GITIGNORE_ENTRY = ".mutagen/"


def _ensure_gitignored(root: Path) -> None:
    """.mutagen/cache holds raw prompts and replies — i.e. the user's own
    code — so an un-gitignored .mutagen/ is one `git add -A` away from
    getting committed. Only touches the file on first creation of .mutagen/,
    and only appends; existing content is never altered."""
    gitignore = Path(root) / ".gitignore"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        if any(
            line.strip() in (_GITIGNORE_ENTRY, ".mutagen")
            for line in existing.splitlines()
        ):
            return
        separator = "" if not existing or existing.endswith("\n") else "\n"
        addition = (
            f"{separator}# mutagen-cli: raw LLM prompts/replies (your code) — don't commit\n"
            f"{_GITIGNORE_ENTRY}\n"
        )
        gitignore.write_text(existing + addition, encoding="utf-8")
    except OSError:
        pass  # best-effort hygiene, never fatal to the run


class Cache:
    def __init__(self, root: Path, enabled: bool = True):
        root = Path(root)
        self.dir = root / ".mutagen" / "cache"
        self.enabled = enabled
        if self.enabled:
            first_creation = not (root / ".mutagen").exists()
            self.dir.mkdir(parents=True, exist_ok=True)
            if first_creation:
                _ensure_gitignored(root)

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
        # The cache holds the user's own source (raw prompts/replies), so it
        # must not be world/group readable on a shared host.
        os.chmod(tmp, 0o600)
        tmp.replace(path)

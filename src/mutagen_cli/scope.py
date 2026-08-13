"""Deciding what to mutate: git scope, AST extraction, and function -> test mapping.

Uses the git CLI through subprocess rather than GitPython — one fewer
dependency, and `git` is by definition present in a git repo.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path
from typing import Iterable, Optional

from .models import Target

SKIP_DIRS = {
    ".git", ".venv", "venv", "env", ".env", "node_modules", "__pycache__",
    ".tox", ".nox", "build", "dist", ".mutagen", "site-packages", ".mypy_cache",
    ".pytest_cache", ".eggs", "migrations", ".claude",
}

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class ScopeError(RuntimeError):
    pass


def run_git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise ScopeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def repo_root(start: Path) -> Path:
    try:
        out = run_git(["rev-parse", "--show-toplevel"], start)
    except ScopeError as exc:
        raise ScopeError(f"{start} is not inside a git repository") from exc
    # Resolved so --path's is_relative_to(root) check below still holds when
    # the repo was opened through a symlinked path: git prints the toplevel
    # in whatever form `start` was given, but abs_path.resolve() always
    # follows symlinks, and comparing an unresolved root against a resolved
    # path would falsely report files as outside the repository.
    return Path(out.strip()).resolve()


def is_skipped(rel: str) -> bool:
    return any(part in SKIP_DIRS for part in Path(rel).parts)


def is_test_file(rel: str) -> bool:
    name = Path(rel).name
    if name == "conftest.py":
        return True
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    return any(part in ("tests", "test") for part in Path(rel).parts)


def is_mutable_source(rel: str) -> bool:
    return rel.endswith(".py") and not is_skipped(rel) and not is_test_file(rel)


def default_base_branch(root: Path) -> str:
    """Guess the repository's default branch, without assuming it's "main".

    Tries the remote's advertised HEAD first (accurate whenever the repo was
    cloned normally), then falls back to whichever of main/master exists
    locally. Raises ScopeError with a pointer to --base if neither works —
    e.g. a shallow clone or a fetch that never ran `git remote set-head`.
    """
    try:
        out = run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], root).strip()
        if out:
            return out.rsplit("/", 1)[-1]
    except ScopeError:
        pass

    for candidate in ("main", "master"):
        try:
            run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"], root)
            return candidate
        except ScopeError:
            continue

    raise ScopeError(
        "could not determine the default branch (no origin/HEAD and no local "
        "main or master). Pass it explicitly with --base <branch>."
    )


def _resolve_base(root: Path, base: str) -> str:
    """Prefer the merge base so we diff against the fork point, not the tip."""
    try:
        return run_git(["merge-base", "HEAD", base], root).strip()
    except ScopeError:
        return base


def changed_line_ranges(root: Path, base: str) -> dict[str, set[int]]:
    """Lines touched relative to `base`, including uncommitted work.

    Diffing the working tree (not HEAD) against the merge base means a run
    picks up edits the user has not committed yet — which is the whole point
    when you are checking tests you just wrote.
    """
    ref = _resolve_base(root, base)
    try:
        diff = run_git(["diff", "--unified=0", "--no-color", ref, "--"], root)
    except ScopeError as exc:
        raise ScopeError(
            f"could not diff against {base!r}. Pass an existing ref with --base, "
            "or use --all / --path."
        ) from exc

    changed: dict[str, set[int]] = {}
    current: Optional[str] = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            continue
        if line.startswith("+++ /dev/null"):
            current = None
            continue
        if current and line.startswith("@@"):
            match = HUNK_RE.match(line)
            if not match:
                continue
            start = int(match.group(1))
            count = int(match.group(2) or 1)
            if count == 0:  # pure deletion; nothing new to mutate
                continue
            changed.setdefault(current, set()).update(range(start, start + count))

    # New files git does not track yet never show up in a diff.
    untracked = run_git(
        ["ls-files", "--others", "--exclude-standard"], root
    ).splitlines()
    for rel in untracked:
        if is_mutable_source(rel):
            try:
                total = len((root / rel).read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeDecodeError):
                continue
            changed.setdefault(rel, set()).update(range(1, total + 1))

    return {p: lines for p, lines in changed.items() if is_mutable_source(p)}


def _is_trivial(node: ast.AST) -> bool:
    body = list(getattr(node, "body", []))
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]  # drop the docstring
    if not body:
        return True
    return all(isinstance(stmt, ast.Pass) for stmt in body)


def functions_in_file(root: Path, rel: str) -> list[Target]:
    """Every non-trivial function/method in a file, with its full source."""
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    lines = text.splitlines()
    targets: list[Target] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not _is_trivial(child):
                    # Include decorators so the model sees the whole unit.
                    start = min(
                        [child.lineno] + [d.lineno for d in child.decorator_list]
                    )
                    end = child.end_lineno or child.lineno
                    targets.append(
                        Target(
                            path=rel,
                            qualname=f"{prefix}{child.name}",
                            start_line=start,
                            end_line=end,
                            source="\n".join(lines[start - 1 : end]),
                        )
                    )
                walk(child, f"{prefix}{child.name}.")

    walk(tree, "")
    return targets


def iter_source_files(root: Path) -> Iterable[str]:
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if is_mutable_source(rel):
            yield rel


def collect_targets(
    root: Path,
    base: Optional[str] = None,
    paths: Optional[list[str]] = None,
    all_files: bool = False,
    max_files: int = 20,
) -> list[Target]:
    """Resolve the run mode into a concrete list of functions to mutate."""
    if paths:
        rels = []
        for raw in paths:
            path = Path(raw)
            abs_path = path if path.is_absolute() else (Path.cwd() / path)
            abs_path = abs_path.resolve()
            if not abs_path.is_relative_to(root):
                raise ScopeError(
                    f"{raw} is outside the repository ({root}). "
                    "--path only accepts files inside it."
                )
            if abs_path.is_dir():
                rels.extend(
                    p.relative_to(root).as_posix()
                    for p in sorted(abs_path.rglob("*.py"))
                )
            else:
                rels.append(abs_path.relative_to(root).as_posix())
        rels = [r for r in rels if is_mutable_source(r)]
        selected = {r: None for r in rels[:max_files]}
        return [t for rel in selected for t in functions_in_file(root, rel)]

    if all_files:
        rels = list(iter_source_files(root))[:max_files]
        return [t for rel in rels for t in functions_in_file(root, rel)]

    changed = changed_line_ranges(root, base or "main")
    targets: list[Target] = []
    for rel in list(changed)[:max_files]:
        touched = changed[rel]
        for target in functions_in_file(root, rel):
            span = range(target.start_line, target.end_line + 1)
            if touched.intersection(span):
                targets.append(target)
    return targets


# --- function -> test mapping -------------------------------------------------


def find_test_files(root: Path) -> list[str]:
    out = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if is_skipped(rel):
            continue
        name = path.name
        if name.startswith("test_") or name.endswith("_test.py"):
            out.append(rel)
    return out


def map_tests(root: Path, targets: list[Target], limit: int = 3) -> None:
    """Attach likely test files to each target, in place.

    A cheap heuristic beats nothing: running only the tests that mention a
    function is dramatically faster than running the whole suite per mutant,
    and when the heuristic finds nothing we fall back to the full suite rather
    than silently skipping.
    """
    test_files = find_test_files(root)
    contents: dict[str, str] = {}
    for rel in test_files:
        try:
            contents[rel] = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            contents[rel] = ""

    for target in targets:
        module = Path(target.path).stem
        bare = target.qualname.split(".")[-1]
        owner = target.qualname.split(".")[0] if "." in target.qualname else None

        scored: list[tuple[int, str]] = []
        for rel, text in contents.items():
            score = 0
            stem = Path(rel).stem
            if stem in (f"test_{module}", f"{module}_test"):
                score += 3
            if re.search(rf"\b{re.escape(bare)}\b", text):
                score += 2
            if owner and re.search(rf"\b{re.escape(owner)}\b", text):
                score += 2
            if re.search(rf"\b{re.escape(module)}\b", text):
                score += 1
            if score:
                scored.append((score, rel))

        scored.sort(key=lambda item: (-item[0], item[1]))
        target.test_files = [rel for _, rel in scored[:limit]]

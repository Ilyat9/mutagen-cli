import subprocess
import sys

import pytest

from mutagen_cli.scope import (
    ScopeError,
    changed_line_ranges,
    collect_targets,
    default_base_branch,
    functions_in_file,
    is_mutable_source,
    is_test_file,
    map_tests,
    repo_root,
)


def test_test_files_are_recognised():
    assert is_test_file("tests/test_cache.py")
    assert is_test_file("pkg/cache_test.py")
    assert is_test_file("conftest.py")
    assert not is_test_file("victim/cache.py")


def test_source_selection_skips_tests_and_vendored_code():
    assert is_mutable_source("victim/cache.py")
    assert not is_mutable_source("tests/test_cache.py")
    assert not is_mutable_source(".venv/lib/python3.11/site-packages/x.py")
    assert not is_mutable_source("README.md")


def test_functions_are_extracted_with_qualnames(victim_repo):
    targets = functions_in_file(victim_repo, "victim/cache.py")
    names = {t.qualname for t in targets}
    assert "LRUCache.get" in names
    assert "LRUCache.invalidate_prefix" in names
    get = next(t for t in targets if t.qualname == "LRUCache.get")
    assert get.source.lstrip().startswith("def get(")
    assert "move_to_end" in get.source


def test_trivial_functions_are_skipped(tmp_path):
    path = tmp_path / "m.py"
    path.write_text("def stub():\n    pass\n\n\ndef real():\n    return 1\n")
    names = {t.qualname for t in functions_in_file(tmp_path, "m.py")}
    assert names == {"real"}


def test_syntax_error_file_yields_nothing(tmp_path):
    (tmp_path / "broken.py").write_text("def oops(:\n")
    assert functions_in_file(tmp_path, "broken.py") == []


def test_repo_root_is_found(victim_repo):
    assert repo_root(victim_repo) == victim_repo


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin on Windows")
def test_repo_root_resolves_a_symlinked_path(victim_repo, tmp_path):
    # `git rev-parse --show-toplevel` echoes back whatever path form it was
    # invoked from. If a symlink into the repo is used, an unresolved root
    # makes `--path`'s is_relative_to(root) check falsely report files
    # inside the repo as outside it.
    link = tmp_path / "victim_link"
    link.symlink_to(victim_repo)
    assert repo_root(link) == victim_repo.resolve()

    targets = collect_targets(
        repo_root(link), paths=[str(link / "victim" / "cache.py")]
    )
    assert targets


def test_default_base_branch_falls_back_to_local_main(victim_repo):
    assert default_base_branch(victim_repo) == "main"


def test_default_base_branch_falls_back_to_local_master(victim_repo):
    subprocess.run(
        ["git", "branch", "-m", "main", "master"], cwd=victim_repo, check=True
    )
    assert default_base_branch(victim_repo) == "master"


def test_default_base_branch_prefers_origin_head(victim_repo, tmp_path):
    subprocess.run(
        ["git", "checkout", "-q", "-b", "develop"], cwd=victim_repo, check=True
    )
    subprocess.run(["git", "branch", "-D", "main"], cwd=victim_repo, check=True)

    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(bare)], cwd=victim_repo, check=True
    )
    subprocess.run(
        ["git", "push", "-q", "origin", "develop"], cwd=victim_repo, check=True
    )
    subprocess.run(
        ["git", "remote", "set-head", "origin", "develop"], cwd=victim_repo, check=True
    )

    assert default_base_branch(victim_repo) == "develop"


def test_default_base_branch_errors_with_no_candidate(victim_repo):
    subprocess.run(
        ["git", "branch", "-m", "main", "trunk"], cwd=victim_repo, check=True
    )
    with pytest.raises(ScopeError, match="--base"):
        default_base_branch(victim_repo)


def test_uncommitted_edits_are_in_scope(victim_repo):
    subprocess.run(
        ["git", "checkout", "-q", "-b", "feature"], cwd=victim_repo, check=True
    )
    path = victim_repo / "victim" / "pricing.py"
    text = path.read_text().replace("discount = price * percent / 100.0",
                                    "discount = price * percent / 100.0  # tweak")
    path.write_text(text)

    changed = changed_line_ranges(victim_repo, "main")
    assert "victim/pricing.py" in changed

    targets = collect_targets(victim_repo, base="main")
    assert [t.qualname for t in targets] == ["apply_discount"]


def test_untracked_files_are_in_scope(victim_repo):
    (victim_repo / "victim" / "brandnew.py").write_text(
        "def added(x):\n    return x + 1\n"
    )
    targets = collect_targets(victim_repo, base="main")
    assert "added" in {t.qualname for t in targets}


def test_all_mode_covers_the_package(victim_repo):
    targets = collect_targets(victim_repo, all_files=True)
    assert {t.path for t in targets} >= {
        "victim/cache.py", "victim/pricing.py", "victim/pagination.py"
    }
    assert not any("test" in t.path for t in targets)


def test_path_mode_limits_to_one_file(victim_repo):
    targets = collect_targets(victim_repo, paths=[str(victim_repo / "victim" / "text.py")])
    assert {t.path for t in targets} == {"victim/text.py"}


def test_tests_are_mapped_to_their_module(victim_repo):
    targets = collect_targets(victim_repo, all_files=True)
    map_tests(victim_repo, targets)
    pricing = next(t for t in targets if t.qualname == "apply_discount")
    assert "tests/test_pricing.py" in pricing.test_files

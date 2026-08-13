import subprocess

from mutagen_cli.scope import (
    changed_line_ranges,
    collect_targets,
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

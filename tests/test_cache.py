"""Cache directory / .gitignore hygiene."""

from mutagen_cli.cache import Cache


def test_first_cache_creation_gitignores_mutagen_dir(tmp_path):
    Cache(tmp_path, enabled=True)
    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".mutagen/" in gitignore


def test_gitignore_is_created_when_absent(tmp_path):
    assert not (tmp_path / ".gitignore").exists()
    Cache(tmp_path, enabled=True)
    assert (tmp_path / ".gitignore").exists()


def test_existing_gitignore_content_is_preserved(tmp_path):
    (tmp_path / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    Cache(tmp_path, enabled=True)
    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "__pycache__/" in gitignore
    assert "*.pyc" in gitignore
    assert ".mutagen/" in gitignore


def test_already_gitignored_mutagen_dir_is_not_duplicated(tmp_path):
    (tmp_path / ".gitignore").write_text(".mutagen/\n", encoding="utf-8")
    Cache(tmp_path, enabled=True)
    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert gitignore.count(".mutagen/") == 1


def test_second_cache_instantiation_does_not_touch_gitignore_again(tmp_path):
    Cache(tmp_path, enabled=True)
    (tmp_path / ".gitignore").write_text("custom line only\n", encoding="utf-8")
    # .mutagen/ already exists, so this must not re-append the entry.
    Cache(tmp_path, enabled=True)
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "custom line only\n"


def test_disabled_cache_does_not_create_or_touch_gitignore(tmp_path):
    Cache(tmp_path, enabled=False)
    assert not (tmp_path / ".mutagen").exists()
    assert not (tmp_path / ".gitignore").exists()

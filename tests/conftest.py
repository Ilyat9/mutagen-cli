import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "victim_project"


def _git(args, cwd):
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


@pytest.fixture
def victim_repo(tmp_path):
    """A throwaway git repo containing the polygon project."""
    repo = tmp_path / "victim"
    shutil.copytree(FIXTURE, repo)
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "test"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "initial"], repo)
    return repo

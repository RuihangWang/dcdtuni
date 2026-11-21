"""``identity.repository_identity`` names exactly the tracked files that differ from the commit."""
from __future__ import annotations

import subprocess

import pytest

from agent.scripted_api import identity as ident


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(repo), "PATH": "/usr/bin:/bin"})


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    for name in ("a.txt", "dir with space/b.txt", "old.txt"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("one\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_the_first_changed_file_keeps_its_first_character(repo):
    # " M a.txt" is the first porcelain line: a leading-space strip used to eat the "a"
    (repo / "a.txt").write_text("two\n")
    identity = ident.repository_identity(repo)
    assert identity["dirty"] is True
    assert identity["changedTrackedFiles"] == ["a.txt"]


def test_staged_unstaged_spaces_and_renames_are_named_by_their_path(repo):
    (repo / "a.txt").write_text("two\n")
    (repo / "dir with space" / "b.txt").write_text("two\n")
    _git(repo, "add", "dir with space/b.txt")
    _git(repo, "mv", "old.txt", "new.txt")
    changed = ident.repository_identity(repo)["changedTrackedFiles"]
    assert sorted(changed) == ["a.txt", "dir with space/b.txt", "new.txt"]


def test_a_clean_tree_is_not_dirty(repo):
    identity = ident.repository_identity(repo)
    assert identity["dirty"] is False and identity["changedTrackedFiles"] == []

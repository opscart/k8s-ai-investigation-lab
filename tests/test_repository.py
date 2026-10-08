import json

import pytest
from conftest import git

from investigator.contracts import RepositoryPolicy
from investigator.repository import Repository


def test_reads_commit_not_working_tree(repo):
    (repo.root / "app.py").write_text("CHANGED WORKTREE SECRET")
    assert "healthz" in repo.read("app.py", 1, 2).text
    assert "CHANGED" not in repo.read("app.py", 1, 2).text
    assert repo.commit in repo.read("app.py", 1, 2).source


@pytest.mark.parametrize("path", ["../app.py", "/etc/passwd", "answers.txt", ".git/config"])
def test_rejects_non_allowed_path(repo, path):
    with pytest.raises(ValueError):
        repo.read(path, 1, 2)


def test_search_cannot_retrieve_expected_answers(repo):
    assert repo.search("EVALUATION-ANSWER") == []


@pytest.mark.parametrize("start,end", [(0, 2), (2, 1), (1, 121), (100, 101)])
def test_bad_line_ranges(repo, start, end):
    with pytest.raises(ValueError):
        repo.read("app.py", start, end)


def test_symlink_allowlist_rejected(repo):
    (repo.root / "link").symlink_to("answers.txt")
    git(repo.root, "add", "link")
    git(
        repo.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "link",
    )
    with pytest.raises(ValueError, match="symlink"):
        Repository(
            repo.root,
            git(repo.root, "rev-parse", "HEAD"),
            RepositoryPolicy(repository_id="fixture", allowed_files=["link"]),
        )


def test_rejects_mutable_revision(repo):
    with pytest.raises(ValueError):
        Repository(repo.root, "HEAD", repo.policy)


@pytest.mark.parametrize(
    "name,args",
    [
        ("exec", {"command": "cat answers.txt"}),
        ("read_repository_file", {"path": "app.py", "start": True, "end": 2}),
        ("search_repository", {"query": "PORT", "extra": "answers.txt"}),
    ],
)
def test_dispatch_is_allowlisted(repo, name, args):
    with pytest.raises(ValueError):
        repo.dispatch(name, json.dumps(args))

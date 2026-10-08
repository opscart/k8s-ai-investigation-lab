import subprocess

import pytest

from investigator.contracts import Evidence, Incident, RepositoryPolicy
from investigator.repository import Repository


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "app.py").write_text('HEALTH_PATH = "/healthz"\nPORT = 8080\n')
    (tmp_path / "answers.txt").write_text("EVALUATION-ANSWER-MUST-NOT-BE-RETRIEVED")
    git(tmp_path, "add", ".")
    git(
        tmp_path,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    return Repository(
        tmp_path,
        git(tmp_path, "rev-parse", "HEAD"),
        RepositoryPolicy(repository_id="fixture", allowed_files=["app.py"]),
    )


@pytest.fixture
def incident():
    return Incident(
        case_id="fixture",
        workload="default/demo",
        question="Why restart?",
        evidence=[Evidence(id="event", source="synthetic event", text="HTTP 404")],
    )

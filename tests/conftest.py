import subprocess

import pytest

from investigator.contracts import (
    AzureDevOpsCapturePolicy,
    Evidence,
    Incident,
    RepositoryPolicy,
)
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


class FakeAzureDevOps:
    def __init__(self):
        app_commit = "a" * 40
        shared_commit = "b" * 40
        self.logs = {
            7: (
                "Downloaded from central: synthetic artifact\n"
                "[ERROR] startup dependency configuration invalid\n"
                "BUILD FAILURE"
            ),
            6: (f"Build.SourceVersion: {app_commit}\nShared library version: {shared_commit}\n"),
        }
        self.files = {
            ("pipeline-app-demo", app_commit, "azure-pipelines.yml"): (
                "propertyName: billing.service.url\n"
            ),
            (
                "pipeline-shared-library-demo",
                shared_commit,
                "templates/jobs/maven-test.yml",
            ): 'mvn test "-D${PROPERTY_NAME}=${PROPERTY_VALUE}"\n',
        }

    def build(self, build_id):
        return {
            "id": build_id,
            "buildNumber": "20261009.1",
            "status": "completed",
            "result": "failed",
            "sourceBranch": "refs/heads/main",
            "sourceVersion": "a" * 40,
            "definition": {"name": "pipeline-app-demo-ci"},
            "repository": {"name": "pipeline-app-demo"},
        }

    def timeline(self, _build_id):
        return {
            "records": [
                {"name": "Run Maven tests", "result": "failed", "log": {"id": 7}},
                {"name": "Print build provenance", "result": "succeeded", "log": {"id": 6}},
            ]
        }

    def log(self, _build_id, log_id):
        return self.logs[log_id]

    def repository_tree(self, repository, commit):
        return sorted(p for (r, c, p) in self.files if (r, c) == (repository, commit))

    def repository_file(self, repository, commit, path):
        return self.files[(repository, commit, path)]


@pytest.fixture
def ado_client():
    return FakeAzureDevOps()


@pytest.fixture
def ado_policy():
    return AzureDevOpsCapturePolicy.model_validate(
        {
            "organization_url": "https://dev.azure.com/opscartinc",
            "project": "AI-DevOps-POC",
            "workload": "AI-DevOps-POC/pipeline-app-demo-ci",
            "question": "Why did this build fail and what is the smallest supported correction?",
            "failed_step_name": "Run Maven tests",
            "provenance_step_name": "Print build provenance",
            "repositories": [
                {
                    "role": "application",
                    "repository": "pipeline-app-demo",
                    "commit_source": "build_source",
                    "allowed_files": ["azure-pipelines.yml"],
                },
                {
                    "role": "shared_library",
                    "repository": "pipeline-shared-library-demo",
                    "commit_source": "provenance_log",
                    "commit_label": "Shared library version",
                    "allowed_files": ["templates/jobs/maven-test.yml"],
                },
            ],
        }
    )

import pytest

from investigator.azure_devops import AzureDevOpsClient, capture_build
from investigator.contracts import AzureDevOpsCapturePolicy

APP_COMMIT = "a" * 40
SHARED_COMMIT = "b" * 40


def test_capture_build_pins_repositories_and_bounds_model_context(ado_client, ado_policy):
    capture = capture_build(ado_client, ado_policy, 679)
    assert capture.incident.case_id == "azure-devops-build-679"
    assert capture.incident.evidence[1].id == "failed_step_log"
    assert "BUILD FAILURE" in capture.incident.evidence[1].text
    assert "download noise" in capture.incident.evidence[1].text
    assert [(item.role, item.commit) for item in capture.repositories] == [
        ("application", APP_COMMIT),
        ("shared_library", SHARED_COMMIT),
    ]
    assert len(capture.context_evidence) == 2
    assert "billing.service.url" in capture.context_evidence[0].text
    assert all(item.id.startswith("repo_") for item in capture.context_evidence)
    assert capture.content_hashes.keys() == {
        "application:azure-pipelines.yml",
        "shared_library:templates/jobs/maven-test.yml",
    }


def test_capture_does_not_copy_unbounded_provenance_log(ado_client, ado_policy):
    ado_client.logs[6] += "SECRET-MATERIAL-MUST-NOT-BE-CAPTURED"
    serialized = capture_build(ado_client, ado_policy, 679).model_dump_json()
    assert "SECRET-MATERIAL-MUST-NOT-BE-CAPTURED" not in serialized
    assert SHARED_COMMIT in serialized


def test_capture_rejects_ambiguous_shared_commit(ado_client, ado_policy):
    ado_client.logs[6] += f"Shared library version: {'c' * 40}\n"
    with pytest.raises(ValueError, match="unambiguous"):
        capture_build(ado_client, ado_policy, 679)


def test_capture_rejects_different_build_repository(ado_client, ado_policy):
    original = ado_client.build

    def changed(build_id):
        value = original(build_id)
        value["repository"] = {"name": "unexpected"}
        return value

    ado_client.build = changed
    with pytest.raises(ValueError, match="does not match"):
        capture_build(ado_client, ado_policy, 679)


def test_capture_rejects_build_that_is_not_completed_and_failed(ado_client, ado_policy):
    original = ado_client.build

    def succeeded(build_id):
        value = original(build_id)
        value.update(status="completed", result="succeeded")
        return value

    ado_client.build = succeeded
    with pytest.raises(ValueError, match="completed and failed"):
        capture_build(ado_client, ado_policy, 679)


def test_capture_rejects_context_over_budget(ado_client, ado_policy):
    value = ado_policy.model_dump()
    value["context_max_chars"] = 2000
    ado_client.files[("pipeline-app-demo", APP_COMMIT, "azure-pipelines.yml")] = "x" * 3000
    with pytest.raises(ValueError, match="per-item context limit|pack bounds"):
        capture_build(ado_client, AzureDevOpsCapturePolicy.model_validate(value), 679)


def test_capture_is_strict_json_contract(ado_client, ado_policy):
    value = capture_build(ado_client, ado_policy, 679)
    assert value.source_type == "azure_devops_build"
    assert value.source_reference.endswith("buildId=679")


def test_rest_client_keeps_pat_out_of_url(monkeypatch):
    observed = {}

    def request(url, headers, _limit):
        observed.update(url=url, headers=headers)
        return b'{"id":679}'

    monkeypatch.setenv("AZURE_DEVOPS_EXT_PAT", "SYNTHETIC-PAT")
    client = AzureDevOpsClient(
        "https://dev.azure.com/opscartinc",
        "AI-DevOps-POC",
        request=request,
    )
    assert client.build(679)["id"] == 679
    assert "SYNTHETIC-PAT" not in observed["url"]
    assert observed["headers"]["Authorization"].startswith("Basic ")
    assert observed["url"].endswith("build/builds/679?api-version=7.1")


@pytest.mark.parametrize(
    "url",
    [
        "http://dev.azure.com/opscartinc",
        "https://example.com/opscartinc",
        "https://dev.azure.com/opscartinc/extra",
        "https://user@dev.azure.com/opscartinc",
        "https://dev.azure.com:444/opscartinc",
    ],
)
def test_rest_client_rejects_unexpected_organization_urls(url):
    with pytest.raises(ValueError, match="dev.azure.com"):
        AzureDevOpsClient(url, "AI-DevOps-POC")

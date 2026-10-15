import json
from pathlib import Path

import pytest
from conftest import FakeAzureDevOps

from investigator.azure_devops import AzureDevOpsClient, capture_build
from investigator.cli import main
from investigator.contracts import AzureDevOpsCapturePolicy

ROOT = Path(__file__).parents[1] / "examples/azure-devops"
APP, LIB = "a" * 40, "b" * 40
JAVA = "src/main/java/com/opscart/lab/DependencyConfig.java"
TEST = "src/test/java/com/opscart/lab/DependencyConfigTest.java"
LOG = """[INFO] Running com.opscart.lab.DependencyConfigTest
[ERROR] Tests run: 1, Failures: 0, Errors: 1, Skipped: 0 <<< FAILURE! -- in com.opscart.lab.DependencyConfigTest
[ERROR] com.opscart.lab.DependencyConfigTest.startsWithConfiguredDependency -- Time elapsed: 0.030 s <<< ERROR!
java.lang.IllegalStateException: startup dependency configuration invalid
\tat com.opscart.lab.DependencyConfig.billingApiUrl(DependencyConfig.java:10)
\tat com.opscart.lab.DependencyConfigTest.startsWithConfiguredDependency(DependencyConfigTest.java:10)
\tat java.base/java.lang.reflect.Method.invoke(Method.java:569)
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-surefire-plugin:3.5.2:test (default-test)
[ERROR] BUILD FAILURE
"""


def fixture_files(app_extra=None, lib_extra=None):
    files = {}
    for repo, commit, base, extra in (
        ("pipeline-app-demo", APP, ROOT / "pipeline-app-demo", app_extra or {}),
        (
            "pipeline-shared-library-demo",
            LIB,
            ROOT / "pipeline-shared-library-demo",
            lib_extra or {},
        ),
    ):
        for path in base.rglob("*"):
            if path.is_file() and path.suffix != ".md":
                files[(repo, commit, path.relative_to(base).as_posix())] = path.read_text()
        for path, text in extra.items():
            files[(repo, commit, path)] = text
    return files


def make(app_extra=None, lib_extra=None, **discovery):
    client = FakeAzureDevOps()
    client.logs[7] = LOG
    client.files = fixture_files(app_extra, lib_extra)
    # Any read of another commit (such as a branch tip) would raise KeyError.
    policy = AzureDevOpsCapturePolicy.model_validate(
        {
            "organization_url": "https://dev.azure.com/opscartinc",
            "project": "AI-DevOps-POC",
            "workload": "AI-DevOps-POC/pipeline-app-demo-ci",
            "question": "Why did this build fail?",
            "failed_step_name": "Run Maven tests",
            "provenance_step_name": "Print build provenance",
            "repositories": [
                {
                    "role": "application",
                    "repository": "pipeline-app-demo",
                    "commit_source": "build_source",
                },
                {
                    "role": "shared_library",
                    "repository": "pipeline-shared-library-demo",
                    "commit_source": "provenance_log",
                    "commit_label": "Shared library version",
                },
            ],
            "discovery": discovery,
        }
    )
    return client, policy


def selected(capture):
    return {(r.role, r.path): r.reason for r in capture.context_selection.excerpts}


def test_build_679_discovers_pipelines_templates_source_and_test_without_file_list():
    client, policy = make()
    capture = capture_build(client, policy, 679)
    found = selected(capture)
    assert found[("application", "azure-pipelines.yml")] == "pipeline_entry"
    assert (
        found[("shared_library", "templates/pipelines/ci-java-microservices.yml")]
        == "template_reference"
    )
    assert found[("shared_library", "templates/jobs/maven-test.yml")] == "template_reference"
    assert found[("application", JAVA)] == "source_location"
    assert found[("application", TEST)] == "source_location"
    assert found[("application", "pom.xml")] == "build_file"
    text = "\n".join(item.text for item in capture.context_evidence)
    assert "billing.service.url" in text and "billing.api.url" in text
    assert all(r.commit in {APP, LIB} for r in capture.context_selection.excerpts)
    assert [r.commit for r in capture.repositories] == [APP, LIB]
    summary = capture.context_selection
    assert summary.files_selected == 6 and summary.repositories_considered == 2
    assert summary.traversal_files_inspected == 3
    assert summary.selected_chars == sum(len(i.text) for i in capture.context_evidence)
    assert capture.safety.residual_findings == 0
    assert "reason=pipeline_entry" in capture.context_evidence[0].source
    assert "repository=pipeline-app-demo" in capture.context_evidence[0].source


def test_discovery_is_deterministic():
    first = capture_build(*make(), 679).model_dump_json()
    assert first == capture_build(*make(), 679).model_dump_json()


def test_discovery_reads_only_exact_commits():
    client, policy = make()
    reads = []
    original = client.repository_file
    client.repository_file = lambda r, c, p: (reads.append(c), original(r, c, p))[1]
    capture_build(client, policy, 679)
    assert set(reads) == {APP, LIB}


def test_unresolvable_commit_fails_closed():
    client, policy = make()
    client.logs[6] = "Build.SourceVersion: x\n"
    with pytest.raises(ValueError, match="unambiguous"):
        capture_build(client, policy, 679)


def test_explicit_files_remain_an_override_and_are_not_duplicated():
    client, policy = make()
    value = policy.model_dump()
    value["repositories"][0]["allowed_files"] = ["pom.xml"]
    capture = capture_build(client, AzureDevOpsCapturePolicy.model_validate(value), 679)
    pom = [i for i in capture.context_evidence if ":pom.xml:" in i.source]
    assert len(pom) == 1 and "reason=policy_allowed_file" in pom[0].source


def test_template_cycles_and_depth_are_bounded():
    cycle = "stages:\n  - template: ../pipelines/ci-java-microservices.yml\n"
    client, policy = make(lib_extra={"templates/jobs/maven-test.yml": cycle})
    capture = capture_build(client, policy, 679)
    assert capture.context_selection.traversal_cycles_skipped >= 1
    client, policy = make(max_traversal_depth=1)
    capture = capture_build(client, policy, 679)
    assert ("shared_library", "templates/jobs/maven-test.yml") not in selected(capture)
    assert capture.context_selection.files_omitted_by_budget >= 1


def test_unapproved_alias_and_missing_template_are_reported_not_fetched():
    entry = (ROOT / "pipeline-app-demo/azure-pipelines.yml").read_text()
    entry += "  - template: x.yml@other\n  - template: missing.yml@shared\n"
    client, policy = make(app_extra={"azure-pipelines.yml": entry})
    capture = capture_build(client, policy, 679)
    unresolved = capture.context_selection.unresolved_anchors
    assert "unapproved_repository:other" in unresolved
    assert "missing_file:shared_library:missing.yml" in unresolved


def test_rejected_files_and_secret_manifests_are_omitted_without_leaking():
    secret = "kind: Secret\ndata:\n  password: TOP-SECRET-VALUE\n"
    entry = (ROOT / "pipeline-app-demo/azure-pipelines.yml").read_text() + "  - template: s.yml\n"
    client, policy = make(
        app_extra={"azure-pipelines.yml": entry, "s.yml": secret, ".env": "TOKEN=ENV-SECRET-VALUE"}
    )
    capture = capture_build(client, policy, 679)
    serialized = capture.model_dump_json()
    assert "TOP-SECRET-VALUE" not in serialized and "ENV-SECRET-VALUE" not in serialized
    assert capture.context_selection.files_omitted_by_policy >= 2
    assert ("application", "s.yml") not in selected(capture)


def test_oversized_discovered_file_is_omitted_by_budget():
    from investigator.discovery import ResponseTooLarge

    client, policy = make()
    original = client.repository_file

    def read(repo, commit, path):
        if path == "pom.xml":
            raise ResponseTooLarge("too large")
        return original(repo, commit, path)

    client.repository_file = read
    capture = capture_build(client, policy, 679)
    assert ("application", "pom.xml") not in selected(capture)
    assert capture.context_selection.files_omitted_by_budget >= 1


def test_budgets_limit_files_excerpts_and_characters():
    capture = capture_build(*make(max_files_selected=2), 679)
    assert capture.context_selection.files_selected == 2
    assert capture.context_selection.files_omitted_by_budget >= 4
    big = "\n".join(f"line {i} billing" for i in range(400))
    client, policy = make(app_extra={JAVA: big}, max_excerpt_chars=500, max_excerpts_per_file=2)
    capture = capture_build(client, policy, 679)
    java = [r for r in capture.context_selection.excerpts if r.path == JAVA]
    assert 1 <= len(java) <= 2 and all(r.line_end >= r.line_start for r in java)
    assert any(r.line_start <= 10 <= r.line_end for r in java)
    assert all(len(i.text) <= 500 for i in capture.context_evidence if JAVA in i.source)
    value = policy.model_dump()
    value["discovery"]["max_repository_chars"] = 1000
    capture = capture_build(client, AzureDevOpsCapturePolicy.model_validate(value), 679)
    assert capture.context_selection.files_omitted_by_budget >= 1
    for role in ("application", "shared_library"):
        chars = [i.text for i in capture.context_evidence if f"role={role};" in i.source]
        assert sum(map(len, chars)) <= 1000


def test_files_considered_bound_applies():
    capture = capture_build(*make(max_files_considered=3), 679)
    assert capture.context_selection.files_considered == 3
    assert capture.context_selection.files_omitted_by_budget >= 1


def test_policy_requires_files_or_discovery():
    _, policy = make()
    value = policy.model_dump()
    value["discovery"] = None
    with pytest.raises(ValueError, match="allowed_files"):
        AzureDevOpsCapturePolicy.model_validate(value)


def fingerprint(capsys, path):
    assert main(["preview", "--mode", "context-pack", "--capture", str(path)]) == 0
    return capsys.readouterr().out


def test_preview_shows_selection_and_binds_it_to_the_fingerprint(tmp_path, capsys):
    capture = capture_build(*make(), 679)
    path = tmp_path / "c.json"
    path.write_text(capture.model_dump_json())
    output = fingerprint(capsys, path)
    assert '"context_selection"' in output and "reason=template_reference" in output
    digest = output.rsplit("Approval fingerprint: ", 1)[1].strip()
    for edit in (
        lambda d: d["context_selection"].update(files_omitted_by_budget=99),
        lambda d: d["context_selection"]["bounds"].update(max_files_selected=1),
        lambda d: d["context_selection"]["excerpts"][0].update(reason="build_file"),
    ):
        data = json.loads(capture.model_dump_json())
        edit(data)
        path.write_text(json.dumps(data))
        changed = fingerprint(capsys, path).rsplit("Approval fingerprint: ", 1)[1].strip()
        assert changed != digest


def test_capture_with_residual_credential_is_rejected_at_preview(tmp_path, capsys):
    data = json.loads(capture_build(*make(), 679).model_dump_json())
    data["context_evidence"][0]["text"] += "\nAuthorization: Bearer abcdefghijkl-RESIDUAL"
    path = tmp_path / "c.json"
    path.write_text(json.dumps(data))
    assert main(["preview", "--mode", "context-pack", "--capture", str(path)]) == 1
    captured = capsys.readouterr()
    assert "RESIDUAL" not in captured.out + captured.err


def test_redacted_capture_still_previews(tmp_path, capsys):
    client, policy = make(app_extra={JAVA: 'password=hunter2value\n"api_key": "abcd1234efgh"\n'})
    capture = capture_build(client, policy, 679)
    assert "hunter2value" not in capture.model_dump_json()
    path = tmp_path / "c.json"
    path.write_text(capture.model_dump_json())
    assert "Approval fingerprint" in fingerprint(capsys, path)


def test_repository_tree_is_pinned_to_a_commit_and_rejects_branches():
    seen = {}

    def request(url, headers, limit):
        seen["url"] = url
        return json.dumps(
            {"value": [{"path": "/", "isFolder": True}, {"path": "/a/b.yml"}]}
        ).encode()

    client = AzureDevOpsClient("https://dev.azure.com/o", "P", request=request)
    client._headers = {"Authorization": "x"}
    assert client.repository_tree("repo", APP) == ["a/b.yml"]
    assert f"versionDescriptor.version={APP}" in seen["url"]
    assert "versionDescriptor.versionType=commit" in seen["url"]
    with pytest.raises(ValueError, match="immutable commit"):
        client.repository_tree("repo", "main")

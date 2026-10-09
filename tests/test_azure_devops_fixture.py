import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "examples/azure-devops"
APP = FIXTURE / "pipeline-app-demo"
SHARED = FIXTURE / "pipeline-shared-library-demo"


def test_required_fixture_files_exist():
    paths = (
        APP / "README.md",
        APP / "azure-pipelines.yml",
        APP / "pom.xml",
        APP / "src/main/java/com/opscart/lab/DependencyConfig.java",
        APP / "src/test/java/com/opscart/lab/DependencyConfigTest.java",
        SHARED / "README.md",
        SHARED / "templates/pipelines/ci-java-microservices.yml",
        SHARED / "templates/jobs/maven-test.yml",
    )
    assert all(path.is_file() for path in paths)


def test_application_pipeline_references_shared_repository_and_template():
    pipeline = (APP / "azure-pipelines.yml").read_text()
    assert "main" in pipeline
    assert "feature/*" in pipeline
    assert "repository: shared" in pipeline
    assert "type: git" in pipeline
    assert "name: AI-DevOps-POC/pipeline-shared-library-demo" in pipeline
    assert "ref: refs/heads/main" in pipeline
    assert "template: templates/pipelines/ci-java-microservices.yml@shared" in pipeline


def test_property_mismatch_requires_source_and_pipeline_correlation():
    source = (APP / "src/main/java/com/opscart/lab/DependencyConfig.java").read_text()
    test_source = (APP / "src/test/java/com/opscart/lab/DependencyConfigTest.java").read_text()
    pipeline = (APP / "azure-pipelines.yml").read_text()
    assert 'System.getProperty("billing.api.url")' in source
    assert "propertyName: billing.service.url" in pipeline
    assert "billing.api.url" not in pipeline
    assert "startup dependency configuration invalid" in source
    assert "billing.api.url" not in test_source
    assert "DependencyConfig.billingApiUrl()" in test_source


def test_shared_templates_forward_parameters_to_maven():
    stage = (SHARED / "templates/pipelines/ci-java-microservices.yml").read_text()
    job = (SHARED / "templates/jobs/maven-test.yml").read_text()
    assert "stage: CI" in stage
    assert "template: ../jobs/maven-test.yml" in stage
    for name in ("propertyName", "propertyValue", "sharedLibraryVersion"):
        assert f"parameters.{name}" in stage
        assert f"parameters.{name}" in job
    assert 'mvn test "-D${PROPERTY_NAME}=${PROPERTY_VALUE}"' in job
    assert "billing.api.url" not in stage + job
    assert "vmImage: ubuntu-latest" in job


def test_safe_commit_provenance_is_wired_through_templates():
    pipeline = (APP / "azure-pipelines.yml").read_text()
    stage = (SHARED / "templates/pipelines/ci-java-microservices.yml").read_text()
    job = (SHARED / "templates/jobs/maven-test.yml").read_text()
    assert "resources.repositories.shared.version" in pipeline
    assert "sharedLibraryVersion: $(SharedLibraryVersion)" in pipeline
    assert "sharedLibraryVersion: ${{ parameters.sharedLibraryVersion }}" in stage
    assert "SHARED_LIBRARY_VERSION: ${{ parameters.sharedLibraryVersion }}" in job
    assert "$(Build.BuildId)" in job
    assert "$(Build.SourceVersion)" in job
    assert "$SHARED_LIBRARY_VERSION" in job


def test_fixture_has_only_synthetic_urls_and_no_sensitive_or_deployment_content():
    contents = "\n".join(
        path.read_text()
        for path in FIXTURE.rglob("*")
        if path.is_file() and "target" not in path.parts
    )
    assert not re.search(r"(?i)\b(?:pat|secret|credential|token|password|corporate)\b", contents)
    urls = re.findall(r"https?://[^\s\"'<>]+", contents)
    assert urls
    assert all(url == "http://billing-api.invalid" for url in urls)
    assert not re.search(r"(?im)^\s*-?\s*(?:stage|job):\s*deploy", contents)
    assert not re.search(r"(?i)remediat|kubectl|serviceconnection|variablegroup", contents)

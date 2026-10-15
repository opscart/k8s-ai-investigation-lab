"""Offline checks of the example Azure Pipelines workflow; no Azure DevOps or Foundry access."""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

from investigator.azure_devops import capture_build
from investigator.cli import main as investigate

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / "examples/azure-devops/investigation-workflow"
PIPELINE = WORKFLOW / "azure-pipelines.yml"
TEXT = PIPELINE.read_text()
SPEC = importlib.util.spec_from_file_location(
    "workflow_guard", WORKFLOW / "scripts/workflow_guard.py"
)
guard = importlib.util.module_from_spec(SPEC)
# Keep bytecode out of examples/, which other tests scan as text.
sys.dont_write_bytecode = True
SPEC.loader.exec_module(guard)

FP = "a" * 64


@pytest.fixture(scope="module")
def pipeline():
    return yaml.safe_load(TEXT)


def stage(pipeline, name):
    return next(item for item in pipeline["stages"] if item["stage"] == name)


def steps(pipeline, name):
    return stage(pipeline, name)["jobs"][0]["steps"]


def step_text(pipeline, name):
    return yaml.safe_dump(stage(pipeline, name))


def test_manual_only_with_required_parameters(pipeline):
    assert pipeline["trigger"] == "none" and pipeline["pr"] == "none"
    params = {item["name"]: item for item in pipeline["parameters"]}
    for required in ("failedBuildId", "azureServiceConnection", "approvalReviewers"):
        assert "default" not in params[required]
    assert params["failedBuildId"]["type"] == "number"
    assert "resources" not in pipeline


def test_stages_run_in_order_without_overriding_conditions(pipeline):
    names = [item["stage"] for item in pipeline["stages"]]
    assert names == ["Capture", "Approval", "Analyze", "Evaluate"]
    assert [item.get("dependsOn") for item in pipeline["stages"]] == [
        None,
        "Capture",
        "Approval",
        "Analyze",
    ]
    for item in pipeline["stages"]:
        assert "condition" not in item
    analyze = step_text(pipeline, "Analyze")
    assert "condition" not in analyze and "continueOnError" not in analyze


def test_model_and_cloud_access_is_confined_to_analyze(pipeline):
    for name in ("Capture", "Approval", "Evaluate"):
        text = step_text(pipeline, name)
        assert "investigate run" not in text and "AzureCLI@2" not in text
        assert "azureSubscription" not in text
    assert step_text(pipeline, "Analyze").count("investigate run --mode context-pack") == 1
    assert TEXT.count("AzureCLI@2") == 1 and TEXT.count("investigate run") == 1
    kinds = [item.get("task") or "bash" for item in steps(pipeline, "Analyze")]
    assert kinds.index("AzureCLI@2") == len(kinds) - 2


def test_capture_uses_existing_clis_and_publishes_review_artifact(pipeline):
    text = step_text(pipeline, "Capture")
    for command in (
        "investigate-ado capture",
        "investigate preview --mode context-pack",
        "fingerprint",
        "manifest",
    ):
        assert command in text
    published = [
        s for s in steps(pipeline, "Capture") if s.get("task") == "PublishPipelineArtifact@1"
    ]
    assert [s["inputs"]["artifact"] for s in published] == ["investigation-review"]
    assert "config/azure-devops-build.example.json" in TEXT
    assert (ROOT / "config/azure-devops-build.example.json").is_file()


def test_access_token_is_mapped_only_to_the_capture_step(pipeline):
    assert TEXT.count("System.AccessToken") == 1
    holders = [s for s in steps(pipeline, "Capture") if "AZURE_DEVOPS_EXT_PAT" in s.get("env", {})]
    assert len(holders) == 1
    assert holders[0]["env"] == {"AZURE_DEVOPS_EXT_PAT": "$(System.AccessToken)"}
    assert "investigate-ado capture" in holders[0]["bash"]
    assert "AZURE_DEVOPS_EXT_PAT" not in TEXT.replace("AZURE_DEVOPS_EXT_PAT: $(System", "")


def test_approval_is_agentless_manual_validation_that_rejects_on_timeout(pipeline):
    job = stage(pipeline, "Approval")["jobs"][0]
    assert job["pool"] == "server"
    (task,) = job["steps"]
    assert task["task"] == "ManualValidation@1"
    assert task["inputs"]["onTimeout"] == "reject"
    instructions = task["inputs"]["instructions"].lower()
    for required in ("capture.json", "preview.txt", "this run", "reject", "sanitized"):
        assert required in instructions
    assert "edit" not in instructions.replace("does not provide any way to change", "")


def test_analyze_consumes_the_artifact_capture_published_and_verifies_first(pipeline):
    published = {
        s["inputs"]["artifact"]
        for s in steps(pipeline, "Capture")
        if s.get("task") == "PublishPipelineArtifact@1"
    }
    items = steps(pipeline, "Analyze")
    downloads = [s for s in items if s.get("task") == "DownloadPipelineArtifact@2"]
    assert [s["inputs"]["artifactName"] for s in downloads] == ["investigation-review"]
    assert downloads[0]["inputs"]["buildType"] == "current"
    assert downloads[0]["inputs"]["artifactName"] in published
    positions = {s.get("displayName"): i for i, s in enumerate(items)}
    verify = positions["Verify fingerprint before any cloud call"]
    assert positions["Download the approved review artifact (this run)"] < verify
    assert verify < next(i for i, s in enumerate(items) if s.get("task") == "AzureCLI@2")
    verify_text = items[verify]["bash"]
    assert "investigate preview --mode context-pack" in verify_text
    assert 'workflow_guard.py" verify' in verify_text and "--run-id" in verify_text
    run = next(s for s in items if s.get("task") == "AzureCLI@2")["inputs"]
    assert run["azureSubscription"] == "${{ parameters.azureServiceConnection }}"
    assert "--approve" in run["inlineScript"] and "fingerprint.txt" in run["inlineScript"]


def test_outputs_use_fresh_directories_and_nothing_is_written_to_git(pipeline):
    assert "mkdir -p" not in TEXT and TEXT.count('mkdir "') == 3
    for forbidden in ("git push", "git commit", "git add", "az pipelines", "az devops", "kubectl"):
        assert forbidden not in TEXT
    assert TEXT.count("persistCredentials: false") == TEXT.count("checkout: self") == 3
    assert "repositories:" not in TEXT


def test_logs_do_not_expose_secrets_or_provider_details():
    for forbidden in ("set -x", "set -o xtrace", "--debug", "printenv", "env |", "curl "):
        assert forbidden not in TEXT
    for line in TEXT.splitlines():
        if line.strip().startswith(("echo", "print")):
            assert "AccessToken" not in line and "AZURE_DEVOPS_EXT_PAT" not in line


def test_evaluation_treats_exit_3_as_expected_and_never_supplies_a_review(pipeline):
    text = step_text(pipeline, "Evaluate")
    assert "--review" not in TEXT
    assert "EXPECTED" in text and "not a failed diagnosis" in text
    assert "evals/matrix/azure-devops-build-679.json" in text
    assert (ROOT / "evals/matrix/azure-devops-build-679.json").is_file()
    publish = next(
        s for s in steps(pipeline, "Evaluate") if s.get("task") == "PublishPipelineArtifact@1"
    )
    assert (
        "'3'" in publish["condition"]
        and publish["inputs"]["artifact"] == "investigation-evaluation"
    )
    gate = steps(pipeline, "Evaluate")[-1]
    assert "'1', '2'" in gate["condition"] and "'3'" not in gate["condition"]
    assert re.search(r"context-pack-\$\{fingerprint:0:12\}\.json", text)


def test_documentation_lists_setup_and_warnings():
    docs = (WORKFLOW / "README.md").read_text().lower()
    for topic in (
        "service connection",
        "system.accesstoken",
        "azure_ai_project_endpoint",
        "azure_ai_model_deployment_name",
        "failedbuildid",
        "retention",
        "manualvalidation",
        "production deployment",
    ):
        assert topic in docs


# --- workflow_guard ---------------------------------------------------------------


def preview(fingerprint=FP, body="evidence"):
    return f"{{}}\n\n{body}\n\nReview/edit inputs before sending.\nApproval fingerprint: {fingerprint}\n"


def review_dir(tmp_path, build=679, run=42, text=None):
    directory = tmp_path / "review"
    directory.mkdir()
    (directory / "capture.json").write_text('{"x": 1}')
    (directory / "preview.txt").write_text(text or preview())
    (directory / "fingerprint.txt").write_text(FP + "\n")
    manifest = guard.build_manifest(directory, build, run)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


def test_fingerprint_extraction_is_strict():
    assert guard.extract_fingerprint(preview()) == FP
    for bad in (
        "no fingerprint\n",
        preview() + "trailing text\n",
        f"Approval fingerprint: {FP}\n" + preview(),
        preview("A" * 64),
        preview("a" * 63),
    ):
        with pytest.raises(guard.GuardError):
            guard.extract_fingerprint(bad)


def test_real_preview_output_matches_extractor(tmp_path, capsys, ado_client, ado_policy):
    path = tmp_path / "capture.json"
    path.write_text(capture_build(ado_client, ado_policy, 679).model_dump_json())
    assert investigate(["preview", "--mode", "context-pack", "--capture", str(path)]) == 0
    output = capsys.readouterr().out
    assert guard.extract_fingerprint(output) == output.rsplit("fingerprint: ", 1)[1].strip()


def test_verify_accepts_untouched_review_and_returns_fingerprint(tmp_path):
    directory = review_dir(tmp_path)
    assert guard.verify(directory, 679, 42, preview()) == FP


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: (d / "capture.json").write_text('{"x": 2}'),
        lambda d: (d / "preview.txt").write_text(preview(body="other")),
        lambda d: (d / "fingerprint.txt").write_text("b" * 64),
        lambda d: (d / "extra.txt").write_text("x"),
        lambda d: (d / "manifest.json").unlink(),
    ],
)
def test_verify_rejects_any_change_after_approval(tmp_path, mutate):
    directory = review_dir(tmp_path)
    mutate(directory)
    with pytest.raises(guard.GuardError):
        guard.verify(directory, 679, 42, preview())


def test_verify_rejects_other_run_other_build_and_fingerprint_mismatch(tmp_path):
    directory = review_dir(tmp_path)
    with pytest.raises(guard.GuardError, match="this pipeline run"):
        guard.verify(directory, 679, 43, preview())
    with pytest.raises(guard.GuardError, match="different failed build"):
        guard.verify(directory, 680, 42, preview())
    with pytest.raises(guard.GuardError, match="does not match"):
        guard.verify(directory, 679, 42, preview("c" * 64))


def test_cli_exit_codes_never_echo_evidence_and_never_overwrite(tmp_path, capsys):
    directory = review_dir(tmp_path)
    recheck = tmp_path / "recheck.txt"
    recheck.write_text(preview("c" * 64, body="SECRET-LOOKING-EVIDENCE"))
    args = ["verify", "--directory", str(directory), "--failed-build-id", "679", "--run-id", "42"]
    assert guard.main([*args, "--preview", str(recheck)]) == 1
    captured = capsys.readouterr()
    assert "SECRET-LOOKING-EVIDENCE" not in captured.out + captured.err
    recheck.write_text(preview())
    assert guard.main([*args, "--preview", str(recheck)]) == 0
    out = tmp_path / "fp.txt"
    assert guard.main(["fingerprint", "--preview", str(recheck), "--output", str(out)]) == 0
    assert out.read_text() == FP + "\n"
    assert guard.main(["fingerprint", "--preview", str(recheck), "--output", str(out)]) == 1

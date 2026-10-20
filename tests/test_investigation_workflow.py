"""Offline checks of the reusable Azure Pipelines template; no Azure DevOps or Foundry access."""

import copy
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
TEMPLATE = ROOT / "templates/azure-devops/investigate-failed-build.yml"
WORKFLOW = ROOT / "examples/azure-devops/investigation-workflow"
WRAPPER = WORKFLOW / "azure-pipelines.yml"
CONSUMER = WORKFLOW / "consumer-example.yml"
TEXT = TEMPLATE.read_text()
SPEC = importlib.util.spec_from_file_location(
    "workflow_guard", ROOT / "templates/azure-devops/scripts/workflow_guard.py"
)
guard = importlib.util.module_from_spec(SPEC)
# Keep bytecode out of the repository tree.
sys.dont_write_bytecode = True
SPEC.loader.exec_module(guard)

FP = "a" * 64
CONDITION = re.compile(r"\$\{\{ if (eq|ne)\(parameters\.(\w+), '([^']*)'\) \}\}")
PARAMETER = re.compile(r"\$\{\{ parameters\.(\w+) \}\}")
DEFAULTS = {
    "failedBuildId": 1234,
    "frameworkRepository": "self",
    "capturePolicy": "policy/capture.json",
    "azureServiceConnection": "SERVICE-CONNECTION",
    "foundryVariableGroup": "FOUNDRY-GROUP",
    "approvalReviewers": "reviewer@example.invalid",
    "approvalTimeoutMinutes": 60,
    "evaluationCase": "",
}


def expand(node, values):
    """Resolve the small compile-time grammar the template uses (if eq/ne, parameters.X)."""

    if isinstance(node, list):
        result = []
        for item in node:
            if isinstance(item, dict) and len(item) == 1:
                ((key, body),) = item.items()
                match = CONDITION.fullmatch(key) if isinstance(key, str) else None
                if match:
                    op, name, literal = match.groups()
                    if (str(values[name]) == literal) == (op == "eq"):
                        result.extend(expand(body, values))
                    continue
            result.append(expand(item, values))
        return result
    if isinstance(node, dict):
        return {key: expand(value, values) for key, value in node.items()}
    if isinstance(node, str):
        whole = PARAMETER.fullmatch(node)
        if whole:
            return values[whole.group(1)]
        return PARAMETER.sub(lambda m: str(values[m.group(1)]), node)
    return node


def render(**overrides):
    values = {**DEFAULTS, **overrides}
    document = yaml.safe_load(TEXT)
    return copy.deepcopy(expand(document["stages"], values))


def stage(stages, name):
    return next(item for item in stages if item["stage"] == name)


def steps(stages, name):
    return stage(stages, name)["jobs"][0]["steps"]


def dump(stages, name=None):
    return yaml.safe_dump(stage(stages, name) if name else stages)


@pytest.fixture
def stages():
    return render()


def test_template_contains_no_service_or_build_specific_values():
    lowered = TEXT.lower()
    for forbidden in (
        "679",
        "pipeline-app-demo",
        "pipeline-shared-library",
        "billing",
        "dev.azure.com",
        "opscartinc",
        "ai-devops-poc",
        "k8s-ai-investigation-foundry",
        "azure-devops-build.example",
        "evals/matrix",
        "services.ai.azure.com",
        "gpt-",
    ):
        assert forbidden not in lowered


def test_template_is_a_parameterized_stage_template_with_no_triggers():
    document = yaml.safe_load(TEXT)
    assert set(document) == {"parameters", "stages"}
    params = {item["name"]: item for item in document["parameters"]}
    assert set(params) == set(DEFAULTS)
    required = {"failedBuildId", "capturePolicy", "azureServiceConnection"}
    required |= {"foundryVariableGroup", "approvalReviewers"}
    assert {name for name, item in params.items() if "default" not in item} == required
    assert params["frameworkRepository"]["default"] == "self"
    assert params["evaluationCase"]["default"] == ""
    assert params["failedBuildId"]["type"] == "number"


def test_stages_run_in_order_without_overriding_conditions(stages):
    assert [item["stage"] for item in stages] == ["Capture", "Approval", "Analyze", "Evaluate"]
    assert [item.get("dependsOn") for item in stages] == [None, "Capture", "Approval", "Analyze"]
    for item in stages:
        assert "condition" not in item
    analyze = dump(stages, "Analyze")
    assert "condition" not in analyze and "continueOnError" not in analyze


def test_self_framework_checks_out_only_the_repository_itself():
    stages = render(frameworkRepository="self")
    for name in ("Capture", "Analyze"):
        checkouts = [s for s in steps(stages, name) if "checkout" in s]
        assert [(s["checkout"], s["path"]) for s in checkouts] == [("self", "framework")]
    capture_vars = {
        v["name"]: v["value"] for v in stage(stages, "Capture")["variables"] if "name" in v
    }
    assert capture_vars["consumerDir"] == capture_vars["frameworkDir"]


def test_external_framework_checkout_is_explicit_and_separate_from_consumer():
    stages = render(frameworkRepository="framework")
    capture = [s for s in steps(stages, "Capture") if "checkout" in s]
    assert [(s["checkout"], s["path"]) for s in capture] == [
        ("framework", "framework"),
        ("self", "consumer"),
    ]
    # Analyze needs only the framework: the consumer repository is not checked out.
    analyze = [s for s in steps(stages, "Analyze") if "checkout" in s]
    assert [(s["checkout"], s["path"]) for s in analyze] == [("framework", "framework")]
    capture_vars = {
        v["name"]: v["value"] for v in stage(stages, "Capture")["variables"] if "name" in v
    }
    assert capture_vars["consumerDir"] != capture_vars["frameworkDir"]
    assert "frameworkRepository" not in dump(stages)


@pytest.mark.parametrize("framework", ["self", "framework"])
def test_installs_only_from_the_framework_checkout_and_never_persists_credentials(framework):
    stages = render(frameworkRepository=framework, evaluationCase="case.json")
    installs = [
        s["bash"]
        for item in stages
        for job in item["jobs"]
        for s in job["steps"]
        if "pip install" in s.get("bash", "")
    ]
    assert len(installs) == 3
    for install in installs:
        assert 'pip install --quiet "$(frameworkDir)"' in install
    checkouts = [
        s for item in stages for job in item["jobs"] for s in job["steps"] if "checkout" in s
    ]
    assert checkouts and all(s["persistCredentials"] is False for s in checkouts)
    assert all("fetchDepth" not in s for s in checkouts)


def test_capture_policy_is_a_validated_parameter_not_hardcoded():
    stages = render(capturePolicy="my/policy.json")
    capture = next(
        s for s in steps(stages, "Capture") if "investigate-ado capture" in s.get("bash", "")
    )
    assert capture["env"]["CAPTURE_POLICY"] == "my/policy.json"
    assert '--policy "$(consumerDir)/$CAPTURE_POLICY"' in capture["bash"]
    assert '""|/*|*..*)' in capture["bash"]
    assert "config/" not in TEXT


def test_variable_group_and_service_connection_are_parameters_confined_to_their_stages():
    stages = render(foundryVariableGroup="GROUP-X", azureServiceConnection="CONN-X")
    for name in ("Capture", "Analyze"):
        assert {"group": "GROUP-X"} in stage(stages, name)["variables"]
    for name in ("Approval", "Evaluate"):
        assert "GROUP-X" not in dump(stages, name)
    assert "CONN-X" not in dump(stages, "Capture") + dump(stages, "Approval")
    assert "CONN-X" not in dump(render(evaluationCase="c.json"), "Evaluate")
    runs = [s for s in steps(stages, "Analyze") if s.get("task") == "AzureCLI@2"]
    assert [s["inputs"]["azureSubscription"] for s in runs] == ["CONN-X"]
    assert TEXT.count("AzureCLI@2") == 1 and TEXT.count("azureSubscription") == 1


def test_model_access_is_confined_to_one_azure_cli_step(stages):
    assert TEXT.count("investigate run") == 1
    for name in ("Capture", "Approval", "Evaluate"):
        assert "investigate run" not in dump(stages, name)
    assert "--mode context-pack" in TEXT and "--mode agent" not in TEXT
    assert "--mode baseline" not in TEXT


def test_access_token_is_mapped_only_to_the_capture_step(stages):
    assert TEXT.count("System.AccessToken") == 1
    holders = [s for s in steps(stages, "Capture") if "AZURE_DEVOPS_EXT_PAT" in s.get("env", {})]
    assert len(holders) == 1
    assert holders[0]["env"]["AZURE_DEVOPS_EXT_PAT"] == "$(System.AccessToken)"
    assert "investigate-ado capture" in holders[0]["bash"]
    assert TEXT.count("AZURE_DEVOPS_EXT_PAT") == 1


def test_capture_publishes_review_artifact_and_makes_no_model_request(stages):
    text = dump(stages, "Capture")
    for command in ("investigate-ado capture", "investigate preview --mode context-pack"):
        assert command in text
    for name in ("fingerprint", "manifest"):
        assert f'workflow_guard.py" {name}' in TEXT
    published = [
        s for s in steps(stages, "Capture") if s.get("task") == "PublishPipelineArtifact@1"
    ]
    assert [s["inputs"]["artifact"] for s in published] == ["investigation-review"]
    assert "AZURE_AI" in text and "azureSubscription" not in text


def test_approval_is_agentless_and_links_to_this_runs_review_artifact(stages):
    job = stage(stages, "Approval")["jobs"][0]
    assert job["pool"] == "server"
    (task,) = job["steps"]
    assert task["task"] == "ManualValidation@1"
    assert task["inputs"]["onTimeout"] == "reject"
    instructions = task["inputs"]["instructions"]
    assert (
        "$(System.CollectionUri)$(System.TeamProject)/_build/results"
        "?buildId=$(Build.BuildId)&view=artifacts&type=publishedArtifacts"
    ) in instructions
    published = [
        s["inputs"]["artifact"]
        for s in steps(stages, "Capture")
        if s.get("task") == "PublishPipelineArtifact@1"
    ]
    for required in ("preview.txt", "capture.json", "sanitized", "Reject"):
        assert required in instructions
    assert f'"{published[0]}"' in instructions
    assert "editable" not in instructions.lower()
    assert task["inputs"]["notifyUsers"] == DEFAULTS["approvalReviewers"]


def test_analyze_consumes_capture_artifact_and_verifies_before_the_model_request(stages):
    published = {
        s["inputs"]["artifact"]
        for s in steps(stages, "Capture")
        if s.get("task") == "PublishPipelineArtifact@1"
    }
    items = steps(stages, "Analyze")
    downloads = [i for i, s in enumerate(items) if s.get("task") == "DownloadPipelineArtifact@2"]
    verify = next(
        i for i, s in enumerate(items) if 'workflow_guard.py" verify' in s.get("bash", "")
    )
    run = next(i for i, s in enumerate(items) if s.get("task") == "AzureCLI@2")
    assert [items[i]["inputs"]["artifactName"] for i in downloads] == ["investigation-review"]
    assert items[downloads[0]]["inputs"]["buildType"] == "current"
    assert items[downloads[0]]["inputs"]["artifactName"] in published
    assert downloads[0] < verify < run
    verify_text = items[verify]["bash"]
    assert "investigate preview --mode context-pack" in verify_text
    assert "--run-id" in verify_text and "--failed-build-id 1234" in verify_text
    script = items[run]["inputs"]["inlineScript"]
    assert "--approve" in script and "fingerprint.txt" in script
    assert "set -euo pipefail" in verify_text and "set -euo pipefail" in script


def test_outputs_use_fresh_directories_and_nothing_is_written_to_git():
    assert "mkdir -p" not in TEXT and TEXT.count('mkdir "') == 3
    for forbidden in (
        "git push",
        "git commit",
        "git add",
        "git checkout",
        "az pipelines",
        "az devops",
        "kubectl",
        "remediat",
    ):
        assert forbidden not in TEXT
    for forbidden in ("trigger:", "pr:", "resources:", "schedules:"):
        assert forbidden not in TEXT


def test_logs_do_not_expose_secrets_or_provider_details():
    for forbidden in ("set -x", "set -o xtrace", "--debug", "printenv", "env |", "curl "):
        assert forbidden not in TEXT
    for line in TEXT.splitlines():
        if line.strip().startswith(("echo", "print")):
            assert "AccessToken" not in line and "AZURE_DEVOPS_EXT_PAT" not in line


def test_evaluation_is_optional_and_skips_successfully_when_blank():
    blank = steps(render(evaluationCase=""), "Evaluate")
    assert len(blank) == 1 and "skipped" in blank[0]["bash"].lower()
    assert "investigate-eval" not in yaml.safe_dump(blank) and "task" not in blank[0]
    supplied = steps(render(evaluationCase="eval/case.json"), "Evaluate")
    text = yaml.safe_dump(supplied)
    assert "investigate-eval evaluate" in text
    evaluate = next(s for s in supplied if "investigate-eval" in s.get("bash", ""))
    assert evaluate["env"] == {"EVALUATION_CASE": "eval/case.json"}
    assert '--case "$(consumerDir)/$EVALUATION_CASE"' in evaluate["bash"]
    assert '""|/*|*..*)' in evaluate["bash"]
    assert "--review" not in TEXT


def test_evaluation_treats_exit_3_as_expected_and_publishes_reports():
    supplied = steps(render(evaluationCase="eval/case.json"), "Evaluate")
    text = yaml.safe_dump(supplied)
    assert "EXPECTED" in text and "not a failed diagnosis" in text
    publish = next(s for s in supplied if s.get("task") == "PublishPipelineArtifact@1")
    assert "'3'" in publish["condition"]
    assert publish["inputs"]["artifact"] == "investigation-evaluation"
    gate = supplied[-1]
    assert "'1', '2'" in gate["condition"] and "'3'" not in gate["condition"]
    assert re.search(r"context-pack-\$\{fingerprint:0:12\}\.json", text)
    downloads = [
        s["inputs"]["artifactName"] for s in supplied if "task" in s and "Download" in s["task"]
    ]
    assert downloads == ["investigation-review", "investigation-result"]


def supplied_parameters(path):
    document = yaml.safe_load(path.read_text())
    (invocation,) = [s for s in document["stages"] if "template" in s]
    return document, invocation


def test_thin_wrapper_invokes_the_template_with_self_framework():
    document, invocation = supplied_parameters(WRAPPER)
    text = WRAPPER.read_text()
    assert invocation["template"] == "/templates/azure-devops/investigate-failed-build.yml"
    assert 30 <= len(text.splitlines()) <= 80
    assert len(document["stages"]) == 1
    for forbidden in ("bash:", "task:", "checkout", "script:", "pool:"):
        assert forbidden not in text
    assert document["trigger"] == "none" and document["pr"] == "none"
    params = invocation["parameters"]
    assert params["frameworkRepository"] == "self"
    assert (ROOT / params["capturePolicy"]).is_file()
    assert params["foundryVariableGroup"] == "k8s-ai-investigation-foundry"
    template_names = {item["name"] for item in yaml.safe_load(TEXT)["parameters"]}
    assert set(params) == template_names
    # Build 679 appears nowhere in the wrapper; evaluation is opt-in at queue time.
    assert "679" not in text
    declared = {item["name"]: item for item in document["parameters"]}
    assert declared["evaluationCase"]["default"] == ""


def test_consumer_example_pins_the_framework_and_owns_its_policy():
    document, invocation = supplied_parameters(CONSUMER)
    (repository,) = document["resources"]["repositories"]
    assert re.fullmatch(r"refs/tags/\S+|[0-9a-f]{40}", repository["ref"])
    configured = yaml.safe_dump(document)
    assert "refs/heads" not in configured and not re.search(r"\bmain\b", configured)
    alias = repository["repository"]
    assert invocation["template"] == f"templates/azure-devops/investigate-failed-build.yml@{alias}"
    assert invocation["parameters"]["frameworkRepository"] == alias
    assert not invocation["parameters"]["capturePolicy"].startswith("config/")
    template_names = {item["name"] for item in yaml.safe_load(TEXT)["parameters"]}
    assert set(invocation["parameters"]) <= template_names
    required = {"failedBuildId", "capturePolicy", "azureServiceConnection"}
    required |= {"foundryVariableGroup", "approvalReviewers"}
    assert required <= set(invocation["parameters"])
    assert document["trigger"] == "none" and document["pr"] == "none"


def test_documentation_lists_responsibilities_prerequisites_and_limits():
    docs = (WORKFLOW / "README.md").read_text().lower()
    for topic in (
        "framework repository",
        "consumer repository",
        "refs/tags",
        "capture policy",
        "variable group",
        "service connection",
        "system.accesstoken",
        "azure_ai_project_endpoint",
        "failedbuildid",
        "retention",
        "manualvalidation",
        "production deployment",
        "limitations",
    ):
        assert topic in docs
    architecture = (ROOT / "docs/architecture.md").read_text()
    assert "templates/azure-devops/investigate-failed-build.yml" in architecture


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

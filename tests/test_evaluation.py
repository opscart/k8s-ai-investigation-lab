import copy
import hashlib
import json
import socket
from pathlib import Path

import pytest

from investigator.eval_cli import main
from investigator.evaluation import EvalCase, ReviewBindingError, ReviewRecord, digest, evaluate

SPEC = Path(__file__).parents[1] / "evals/matrix/azure-devops-build-679.json"
APP = "a6394327db976c4b3ab1f3b54a6849207610d22d"
LIB = "1a376de0cc3390f160e914e9c209e513a0d8e0b6"
FILES = [
    ("application", "pipeline-app-demo", APP, "azure-pipelines.yml", "pipeline_entry"),
    (
        "shared_library",
        "pipeline-shared-library-demo",
        LIB,
        "templates/pipelines/ci-java-microservices.yml",
        "template_reference",
    ),
    (
        "shared_library",
        "pipeline-shared-library-demo",
        LIB,
        "templates/jobs/maven-test.yml",
        "template_reference",
    ),
    (
        "application",
        "pipeline-app-demo",
        APP,
        "src/main/java/com/opscart/lab/DependencyConfig.java",
        "source_location",
    ),
    (
        "application",
        "pipeline-app-demo",
        APP,
        "src/test/java/com/opscart/lab/DependencyConfigTest.java",
        "source_location",
    ),
    ("application", "pipeline-app-demo", APP, "pom.xml", "build_file"),
]


def source(role, repo, commit, path, reason):
    return (
        f"role={role}; repository={repo}; Azure DevOps AI-DevOps-POC/{repo}@{commit}:{path}:L1-L9; "
        f"reason={reason}"
    )


def build():
    context = []
    for item in FILES:
        text = source(*item)
        context.append(
            {
                "id": "repo_" + hashlib.sha256(text.encode()).hexdigest()[:24],
                "source": text,
                "text": "x: y",
            }
        )
    incident = [
        {"id": "build_summary", "source": "Azure DevOps build 679", "text": "{}"},
        {"id": "failed_step_log", "source": "Azure DevOps build 679 log 7", "text": "L1: fail"},
        {
            "id": "build_provenance",
            "source": "Azure DevOps build 679 provenance log 6",
            "text": "a=b",
        },
    ]
    repositories = [
        {"role": "application", "repository": "pipeline-app-demo", "commit": APP, "files": []},
        {
            "role": "shared_library",
            "repository": "pipeline-shared-library-demo",
            "commit": LIB,
            "files": [],
        },
    ]
    capture = {
        "incident": {"case_id": "azure-devops-build-679", "evidence": incident},
        "repositories": repositories,
        "context_evidence": context,
        "safety": {"residual_findings": 0},
        "context_selection": {"files_selected": 6},
    }
    ids = [item["id"] for item in context]
    result = {
        "case_id": "azure-devops-build-679",
        "mode": "context-pack",
        "model": "synthetic-deployment",
        "repository": {"service": "x", "repositories": copy.deepcopy(repositories)},
        "diagnosis": {
            "assessment": {
                "text": "The build fails because the pipeline passes billing.service.url "
                "while the source reads billing.api.url.",
                "evidence_ids": ids[:2],
            },
            "cause_status": "supported",
            "likely_cause": {
                "text": "propertyName billing.service.url does not match "
                "billing.api.url. The URL value is not the cause.",
                "evidence_ids": ids,
            },
            "alternatives": [],
            "proposed_correction": "Change propertyName to billing.api.url in azure-pipelines.yml.",
            "verification": [],
            "missing_evidence": [],
        },
        "stats": {
            "model_calls": 1,
            "tool_calls": 0,
            "input_tokens": 5000,
            "output_tokens": 600,
            "retrieved_items": 6,
            "retrieved_chars": 24,
        },
        "evidence_sources": {item["id"]: item["source"] for item in [*incident, *context]},
    }
    return capture, result


@pytest.fixture
def case():
    return EvalCase.model_validate_json(SPEC.read_text())


def digests_for(case, capture, result):
    return {
        "case": digest(SPEC.read_bytes()),
        "capture": digest(json.dumps(capture).encode()),
        "result": digest(json.dumps(result).encode()),
    }


def review_for(case, answers, **changes):
    capture, result = build()
    recorded = digests_for(case, capture, result) | changes
    return ReviewRecord(reviewer="r", digests=recorded, answers=answers)


def run(case, mutate=None, review=None):
    capture, result = build()
    if mutate:
        mutate(capture, result)
    return evaluate(case, capture, result, review, digests_for(case, capture, result))


def gate(report, gate_id):
    return next(g for g in report["hard_gates"] if g["id"] == gate_id)


def test_passing_result_requires_human_review_and_never_claims_correctness(case):
    report = run(case)
    assert report["failed_gates"] == []
    assert report["overall_status"] == "human_review_required"
    assert {q["answer"] for q in report["human_review"]["questions"]} == {"pending"}
    assert "not establish" in report["disclaimer"]
    assert report["statistics"]["excerpts"] == 6


def test_recorded_review_can_pass_or_fail(case):
    answers = {q.id: "pass" for q in case.human_review}
    assert run(case, review=review_for(case, answers))["overall_status"] == "pass"
    answers["calibration"] = "fail"
    assert run(case, review=review_for(case, answers))["overall_status"] == "fail"
    with pytest.raises(ValueError):
        run(case, review=review_for(case, {"bogus": "pass"}))


def wrong_commit(role, value):
    def mutate(capture, result):
        for repo in capture["repositories"]:
            if repo["role"] == role:
                repo["commit"] = value

    return mutate


def test_wrong_application_commit_fails(case):
    report = run(case, wrong_commit("application", "c" * 40))
    assert not gate(report, "commit:application")["passed"]
    assert gate(report, "commit:shared_library")["passed"]


def test_wrong_shared_library_commit_fails(case):
    def mutate(capture, result):
        result["repository"]["repositories"][1]["commit"] = "d" * 40

    assert not gate(run(case, mutate), "commit:shared_library")["passed"]


def test_missing_required_evidence_fails(case):
    def mutate(capture, result):
        capture["context_evidence"].pop()
        result["evidence_sources"].pop(
            next(k for k in result["evidence_sources"] if k.startswith("repo_"))
        )

    report = run(case, mutate)
    assert not gate(report, "required_references")["passed"]
    assert not gate(report, "excerpt_count")["passed"]
    capture_missing = run(
        case,
        lambda c, r: (c["incident"]["evidence"].pop(0), r["evidence_sources"].pop("build_summary")),
    )
    assert not gate(capture_missing, "required_evidence_ids")["passed"]


def test_mutable_branch_evidence_fails(case):
    def mutate(capture, result):
        item = capture["context_evidence"][0]
        item["source"] = item["source"].replace(f"@{APP}", "@main")
        result["evidence_sources"][item["id"]] = item["source"]

    report = run(case, mutate)
    assert not gate(report, "no_mutable_branch_evidence")["passed"]
    assert not gate(report, "repository_evidence_parseable")["passed"]


@pytest.mark.parametrize(
    ("key", "value", "gate_id"),
    [
        ("model_calls", 2, "budget:model_calls"),
        ("tool_calls", 1, "budget:tool_calls"),
        ("input_tokens", 20001, "budget:input_tokens"),
        ("output_tokens", 4001, "budget:output_tokens"),
        ("retrieved_chars", 20001, "budget:retrieved_chars"),
        ("retrieved_items", 13, "budget:retrieved_items"),
    ],
)
def test_budget_excess_fails(case, key, value, gate_id):
    report = run(case, lambda c, r: r["stats"].update({key: value}))
    assert not gate(report, gate_id)["passed"]
    assert report["overall_status"] == "fail"


def test_residual_safety_finding_and_missing_summary_fail(case):
    report = run(case, lambda c, r: c["safety"].update(residual_findings=1))
    assert not gate(report, "residual_findings")["passed"]
    report = run(case, lambda c, r: c.pop("safety"))
    assert not gate(report, "safety_summary_present")["passed"]


def test_credential_in_capture_or_diagnosis_fails(case):
    report = run(case, lambda c, r: c["context_evidence"][0].update(text="password=hunter2value"))
    assert not gate(report, "no_recognizable_credentials")["passed"]
    assert "hunter2value" not in json.dumps(report)


def test_forbidden_url_value_claim_fails_but_negated_wording_does_not(case):
    def blame(capture, result):
        result["diagnosis"]["likely_cause"]["text"] += (
            " The propertyValue is wrong and unreachable."
        )

    assert not gate(run(case, blame), "forbidden_claim:url_value_blamed")["passed"]
    ok = run(case)
    assert gate(ok, "forbidden_claim:url_value_blamed")["passed"]
    assert any(h["id"] == "negated_forbidden_wording_ignored" for h in ok["textual_heuristics"])


def test_required_claim_and_applied_correction_gates(case):
    report = run(
        case,
        lambda c, r: (
            r["diagnosis"]["likely_cause"].update(text="Something failed.")
            or r["diagnosis"]["assessment"].update(text="It broke.")
        ),
    )
    assert not gate(report, "required_claim:property_name_mismatch")["passed"]
    report = run(
        case,
        lambda c, r: r["diagnosis"].update(
            proposed_correction="I have applied the change to propertyName."
        ),
    )
    assert not gate(report, "correction_not_claimed_applied")["passed"]


def test_incorrect_cause_disposition_fails(case):
    report = run(case, lambda c, r: r["diagnosis"].update(cause_status="unresolved"))
    assert not gate(report, "cause_disposition")["passed"]
    insufficient = case.model_copy(update={"disposition": "insufficient"})
    assert not gate(run(insufficient), "cause_disposition")["passed"]


def test_case_id_mismatch_fails(case):
    assert not gate(run(case, lambda c, r: r.update(case_id="other")), "case_ids_match")["passed"]
    capture_other = run(case, lambda c, r: c["incident"].update(case_id="other"))
    assert not gate(capture_other, "case_ids_match")["passed"]


def test_heuristics_are_reported_separately_from_gates(case):
    report = run(
        case, lambda c, r: r["diagnosis"].update(proposed_correction="Change propertyValue.")
    )
    names = {h["id"]: h["matched"] for h in report["textual_heuristics"]}
    assert names["correction_avoids:propertyValue"] is False
    assert report["failed_gates"] == []


def write(tmp_path):
    capture, result = build()
    paths = {name: tmp_path / f"{name}.json" for name in ("capture", "result")}
    paths["capture"].write_text(json.dumps(capture))
    paths["result"].write_text(json.dumps(result))
    return paths


def test_cli_is_offline_deterministic_and_never_overwrites(tmp_path, monkeypatch, capsys):
    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    paths = write(tmp_path)
    outputs = []
    for name in ("one", "two"):
        out = tmp_path / name
        argv = [
            "evaluate",
            "--case",
            str(SPEC),
            "--capture",
            str(paths["capture"]),
            "--result",
            str(paths["result"]),
            "--output",
            str(out),
        ]
        assert main(argv) == 3
        outputs.append(
            ((out / "evaluation.json").read_bytes(), (out / "evaluation.md").read_bytes())
        )
    assert outputs[0] == outputs[1]
    assert b"human_review_required" in outputs[0][1]
    assert main(argv) == 2  # existing output is never overwritten
    capsys.readouterr()


def file_digests(paths, spec=SPEC):
    return {
        "case": digest(spec.read_bytes()),
        "capture": digest(paths["capture"].read_bytes()),
        "result": digest(paths["result"].read_bytes()),
    }


def cli_args(paths, output, spec=SPEC):
    return [
        "evaluate",
        "--case",
        str(spec),
        "--capture",
        str(paths["capture"]),
        "--result",
        str(paths["result"]),
        "--output",
        str(output),
    ]


def test_cli_exit_codes_for_fail_and_pass(tmp_path):
    paths = write(tmp_path)
    case = EvalCase.model_validate_json(SPEC.read_text())
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps(
            {
                "reviewer": "r",
                "digests": file_digests(paths),
                "answers": {q.id: "pass" for q in case.human_review},
            }
        )
    )
    assert main([*cli_args(paths, tmp_path / "p"), "--review", str(review)]) == 0
    result = json.loads(paths["result"].read_text())
    result["stats"]["model_calls"] = 3
    paths["result"].write_text(json.dumps(result))
    assert main(cli_args(paths, tmp_path / "f")) == 1
    paths["result"].write_text("not json")
    assert main(cli_args(paths, tmp_path / "e")) == 2


def test_review_is_invalidated_when_case_capture_or_result_changes(tmp_path, capsys, case):
    paths = write(tmp_path)
    answers = {q.id: "pass" for q in case.human_review}
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps({"reviewer": "r", "digests": file_digests(paths), "answers": answers})
    )
    changed_spec = tmp_path / "case.json"
    changed_spec.write_text(SPEC.read_text() + "\n")
    for name in ("capture", "result", "case"):
        edited = {key: Path(value) for key, value in paths.items()}
        spec = SPEC
        if name == "case":
            spec = changed_spec
        else:
            copy_path = tmp_path / f"changed-{name}.json"
            copy_path.write_text(paths[name].read_text() + " ")
            edited[name] = copy_path
        out = tmp_path / f"out-{name}"
        assert main([*cli_args(edited, out, spec), "--review", str(review)]) == 2
        assert f"changed: {name}" in capsys.readouterr().err
        assert not out.exists()


def test_evaluate_rejects_review_bound_to_other_digests(case):
    answers = {q.id: "pass" for q in case.human_review}
    stale = review_for(case, answers, capture="0" * 64)
    with pytest.raises(ReviewBindingError, match="capture"):
        run(case, review=stale)
    with pytest.raises(ReviewBindingError):
        evaluate(case, *build(), review_for(case, answers), digests=None)


def test_review_requires_digests_in_its_schema():
    with pytest.raises(ValueError):
        ReviewRecord.model_validate({"reviewer": "r", "answers": {}})


def test_claim_gates_show_patterns_and_missing_evidence_is_not_applicable(case):
    from investigator.eval_report import markdown

    def only_supplied(capture, result):
        result["diagnosis"]["assessment"]["text"] = "billing.service.url only."
        result["diagnosis"]["likely_cause"]["text"] = "billing.service.url was supplied."

    required = gate(run(case, only_supplied), "required_claim:property_name_mismatch")
    assert not required["passed"]
    assert required["observed"] == {
        "matched_patterns": [r"billing\.service\.url"],
        "missing_patterns": [r"billing\.api\.url"],
    }
    ok = gate(run(case), "required_claim:property_name_mismatch")
    assert ok["expected"]["required_patterns"] == [r"billing\.service\.url", r"billing\.api\.url"]
    assert ok["observed"]["missing_patterns"] == []
    forbidden = gate(run(case), "forbidden_claim:url_value_blamed")
    assert forbidden["observed"] == {"forbidden_pattern_found_in": "nowhere"}
    assert forbidden["expected"]["allowed_occurrences"] == 0
    heuristics = {h["id"]: h["matched"] for h in run(case)["textual_heuristics"]}
    assert heuristics["reports_missing_evidence"] is None
    text = markdown(run(case))
    assert "not_applicable: reports_missing_evidence" in text
    assert "NOT matched: reports_missing_evidence" not in text
    assert "| [] |" not in text and "[]" not in text.split("## Statistics")[0]
    insufficient = case.model_copy(update={"disposition": "insufficient"})
    heuristics = {h["id"]: h["matched"] for h in run(insufficient)["textual_heuristics"]}
    assert heuristics["reports_missing_evidence"] is False

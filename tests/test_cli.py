import json

from investigator.cli import main, parser, prepare


def args_for(tmp_path, incident):
    path = tmp_path / "incident.json"
    path.write_text(incident.model_dump_json())
    return ["preview", "--mode", "baseline", "--case", str(path)]


def test_preview_needs_no_cloud_credentials(tmp_path, incident, capsys, monkeypatch):
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
    assert main(args_for(tmp_path, incident)) == 0
    assert "Approval fingerprint" in capsys.readouterr().out


def test_run_without_approval_rejected_before_provider(tmp_path, incident, capsys):
    args = args_for(tmp_path, incident)
    args[0] = "run"
    assert main(args) == 1
    assert "approve its exact fingerprint" in capsys.readouterr().err


def test_edit_or_destination_change_invalidates_approval(tmp_path, incident, monkeypatch):
    argv = args_for(tmp_path, incident)
    args = parser().parse_args(argv)
    old = prepare(args)[-1]
    incident.evidence[0].text = "changed"
    args.case.write_text(incident.model_dump_json())
    assert prepare(args)[-1] != old
    second = prepare(args)[-1]
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://another.ai.azure.com/api/projects/p")
    assert prepare(args)[-1] != second


def test_validation_does_not_echo_sensitive_input(tmp_path, incident, capsys):
    argv = args_for(tmp_path, incident)
    value = json.loads(incident.model_dump_json())
    value["extra_secret"] = "NEVER-PRINT-THIS"
    (tmp_path / "incident.json").write_text(json.dumps(value))
    assert main(argv) == 1
    captured = capsys.readouterr()
    assert "NEVER-PRINT-THIS" not in captured.out + captured.err

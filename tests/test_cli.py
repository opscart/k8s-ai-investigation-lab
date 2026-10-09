import json

from investigator.cli import add_cost_estimate, main, parser, prepare


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


def test_provider_failure_reports_stage_and_opt_in_details(tmp_path, incident, capsys, monkeypatch):
    from investigator import foundry

    class FailingProvider:
        stage = "agent_creation"
        cleanup_warnings = []

        def __init__(self, agent_mode):
            pass

        def __enter__(self):
            exc = RuntimeError("DO-NOT-ECHO-EXCEPTION")
            exc.status_code = 400
            exc.body = {"error": {"message": "SYNTHETIC-DETAIL", "param": "tools"}}
            raise exc

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(foundry, "Foundry", FailingProvider)
    argv = args_for(tmp_path, incident)
    argv[0] = "run"
    digest = prepare(parser().parse_args(argv))[-1]
    argv += ["--approve", digest, "--output", str(tmp_path / "results")]
    assert main(argv) == 1
    output = capsys.readouterr().err
    assert '"stage": "agent_creation"' in output
    assert '"http_status": 400' in output
    assert "SYNTHETIC-DETAIL" not in output
    assert main(argv + ["--debug"]) == 1
    output = capsys.readouterr().err
    assert "SYNTHETIC-DETAIL" in output
    assert "DO-NOT-ECHO-EXCEPTION" not in output


def test_context_pack_preview_shows_only_bounded_pack(tmp_path, repo, incident, capsys):
    case = tmp_path / "incident.json"
    case.write_text(incident.model_dump_json())
    policy = tmp_path / "policy.json"
    policy.write_text(repo.policy.model_dump_json())
    registry = tmp_path / "repositories.json"
    registry.write_text(
        json.dumps(
            {
                "service": "default/demo",
                "repositories": [
                    {
                        "role": "application",
                        "root": ".",
                        "commit": repo.commit,
                        "policy": "policy.json",
                    }
                ],
                "context_max_chars": 2000,
                "context_max_items": 1,
            }
        )
    )
    assert (
        main(
            [
                "preview",
                "--mode",
                "context-pack",
                "--case",
                str(case),
                "--repository-set",
                str(registry),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "EXACT BOUNDED REPOSITORY CONTEXT TO BE SENT" in output
    assert "HEALTH_PATH" in output
    assert "EVALUATION-ANSWER" not in output


def test_cost_estimate_is_explicit_and_deterministic():
    stats = {"input_tokens": 1000, "output_tokens": 500}
    add_cost_estimate(stats, 2.0, 8.0)
    assert stats["estimated_cost_usd"] == 0.006
    assert stats["input_price_per_million_usd"] == 2.0

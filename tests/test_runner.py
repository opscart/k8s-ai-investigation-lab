import json

import pytest

from investigator.contracts import Evidence, Limits
from investigator.runner import ToolCall, Turn, run


def answer(ids=None):
    claim = {"text": "The probe returned HTTP 404.", "evidence_ids": ids or ["event"]}
    return json.dumps(
        {
            "assessment": claim,
            "cause_status": "hypothesis",
            "likely_cause": claim,
            "alternatives": [],
            "proposed_correction": "Verify the route.",
            "verification": ["Check the probe route"],
            "missing_evidence": [],
        }
    )


class Fake:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.requests = []

    def respond(self, inputs, remaining_seconds):
        assert remaining_seconds > 0
        self.requests.append(inputs)
        return next(self.turns)


def test_baseline_no_repository_content(incident):
    provider = Fake([Turn(text=answer(), input_tokens=10, output_tokens=20)])
    result = run(provider, incident, None)
    assert "repository_inventory" not in provider.requests[0][0]["content"]
    assert result["stats"]["tool_calls"] == 0
    assert result["stats"]["input_tokens"] == 10
    assert result["stats"]["retrieved_chars"] == 0


def test_context_pack_is_one_call_without_tools(repo, incident):
    context = Evidence(
        id="repo_context",
        source=f"fixture@{repo.commit}:app.py:L1-L2",
        text='1: HEALTH_PATH = "/healthz"',
    )
    provider = Fake([Turn(text=answer(["repo_context"]), input_tokens=20, output_tokens=10)])
    result = run(
        provider,
        incident,
        repo,
        mode="context-pack",
        prefetched_evidence=[context],
    )
    assert result["mode"] == "context-pack"
    assert result["stats"]["model_calls"] == 1
    assert result["stats"]["tool_calls"] == 0
    assert result["stats"]["retrieved_items"] == 1
    assert result["stats"]["retrieved_chars"] == len(context.text)
    assert "HEALTH_PATH" in provider.requests[0][0]["content"]


def test_captured_context_pack_needs_no_local_repository(incident):
    context = Evidence(id="repo_remote", source="Azure DevOps pinned commit", text="1: safe")
    provider = Fake([Turn(text=answer(["repo_remote"]))])
    inventory = {"service": "demo", "repositories": [{"commit": "a" * 40}]}
    result = run(
        provider,
        incident,
        None,
        mode="context-pack",
        prefetched_evidence=[context],
        repository_inventory=inventory,
    )
    assert result["repository"] == inventory
    assert result["stats"]["model_calls"] == 1
    assert result["stats"]["tool_calls"] == 0


def test_context_pack_cannot_call_tools(repo, incident):
    with pytest.raises(ValueError, match="Non-agent"):
        run(
            Fake([Turn(calls=[ToolCall("1", "search_repository", "{}")])]),
            incident,
            repo,
            mode="context-pack",
            prefetched_evidence=[Evidence(id="repo_x", source="fixture", text="safe")],
        )


def test_agent_tools_and_real_provenance(repo, incident):
    provider = Fake(
        [
            Turn(
                calls=[ToolCall("1", "read_repository_file", '{"path":"app.py","start":1,"end":2}')]
            ),
            Turn(text=answer([repo.read("app.py", 1, 2).id])),
        ]
    )
    result = run(provider, incident, repo)
    assert result["stats"]["model_calls"] == 2
    assert result["stats"]["tool_calls"] == 1
    assert "healthz" in provider.requests[1][0]["output"]
    assert "EVALUATION-ANSWER" not in json.dumps(provider.requests)
    assert repo.commit in result["evidence_sources"][repo.read("app.py", 1, 2).id]


@pytest.mark.parametrize("agent_mode", [False, True])
def test_json_mode_instruction_in_input(repo, incident, agent_mode):
    provider = Fake([Turn(text=answer())])
    run(provider, incident, repo if agent_mode else None)
    content = provider.requests[0][0]["content"]
    instruction, payload = content.split("\n", 1)
    assert instruction == "Return the investigation result as JSON."
    assert json.loads(payload)["case_id"] == incident.case_id


def test_unknown_citation_rejected(incident):
    with pytest.raises(ValueError, match="not supplied"):
        run(Fake([Turn(text=answer(["invented"]))]), incident, None)


def test_baseline_cannot_call_tools(incident):
    with pytest.raises(ValueError, match="Non-agent"):
        run(Fake([Turn(calls=[ToolCall("1", "search_repository", "{}")])]), incident, None)


def test_tool_budget(repo, incident):
    turn = Turn(calls=[ToolCall("1", "search_repository", '{"query":"PORT"}')])
    with pytest.raises(ValueError, match="tool-call budget"):
        run(Fake([turn]), incident, repo, Limits(tool_calls=0))


def test_model_budget(repo, incident):
    turn = Turn(calls=[ToolCall("1", "search_repository", '{"query":"PORT"}')])
    with pytest.raises(ValueError, match="model-call budget"):
        run(Fake([turn]), incident, repo, Limits(model_calls=1))


def test_rejected_tools_do_not_leak_arguments(repo, incident):
    provider = Fake(
        [Turn(calls=[ToolCall("1", "exec", '{"secret":"DO-NOT-ECHO"}')]), Turn(text=answer())]
    )
    result = run(provider, incident, repo)
    assert "tool_request_rejected" in provider.requests[1][0]["output"]
    assert "DO-NOT-ECHO" not in json.dumps(result)


def test_initial_context_budget_prevents_provider_call(incident):
    provider = Fake([])
    with pytest.raises(ValueError, match="context budget"):
        run(provider, incident, None, Limits(context_chars=1000))
    assert provider.requests == []


def test_duplicate_tool_call_id(repo, incident):
    call = ToolCall("same", "search_repository", '{"query":"PORT"}')
    with pytest.raises(ValueError, match="Duplicate"):
        run(Fake([Turn(calls=[call, call])]), incident, repo)


def test_reserved_evidence_id(repo, incident):
    incident.evidence[0].id = "repo_forged"
    with pytest.raises(ValueError, match="reserved"):
        run(Fake([]), incident, repo)

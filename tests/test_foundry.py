"""Adapter contract tests using installed SDK types, no Azure calls."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition

from investigator.foundry import Foundry
from investigator.tools import TOOLS


def test_sdk_accepts_function_definitions():
    definition = PromptAgentDefinition(
        model="deployment", instructions="JSON", tools=[FunctionTool(**tool) for tool in TOOLS]
    )
    assert definition.as_dict()["tools"][0]["name"] == "search_repository"


def adapter(agent):
    provider = Foundry.__new__(Foundry)
    provider.agent = agent
    provider.model = "deployment"
    provider.conversation = SimpleNamespace(id="conversation")
    provider.client = Mock()
    provider.client.responses.create.return_value = SimpleNamespace(
        status="completed",
        output_text="{}",
        output=[],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    return provider


def test_agent_reference_pins_version_and_has_request_timeout():
    provider = adapter(SimpleNamespace(name="agent", version="1"))
    assert provider.respond([], 12).input_tokens == 10
    args = provider.client.responses.create.call_args.kwargs
    assert args["extra_body"]["agent_reference"]["version"] == "1"
    assert args["timeout"] == 12
    assert "text" not in args
    assert "model" not in args


def test_baseline_has_no_agent_or_tools():
    provider = adapter(None)
    provider.respond([], 100)
    args = provider.client.responses.create.call_args.kwargs
    assert args["text"] == {"format": {"type": "json_object"}}
    assert args["model"] == "deployment"
    assert "extra_body" not in args
    assert "tools" not in args


def test_incomplete_response_is_not_a_diagnosis():
    provider = adapter(None)
    provider.client.responses.create.return_value.status = "incomplete"
    with pytest.raises(ValueError, match="not complete"):
        provider.respond([], 10)


def test_bad_endpoint_rejected_without_auth(monkeypatch):
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.org/api/projects/a")
    with pytest.raises(ValueError, match="endpoint"):
        Foundry(False)


def test_agent_creation_owns_json_format():
    provider = adapter(None)
    provider.agent_mode = True
    provider.project = Mock()
    provider.__enter__()
    definition = provider.project.agents.create_version.call_args.kwargs["definition"]
    assert definition.as_dict()["text"] == {"format": {"type": "json_object"}}

"""Foundry prompt agent adapter; Azure CLI authentication, no API keys."""

import os
import uuid
from urllib.parse import urlparse

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
from azure.identity import AzureCliCredential

from .runner import ToolCall, Turn, instructions
from .tools import TOOLS


class Foundry:
    def __init__(self, agent_mode: bool):
        endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").rstrip("/")
        self.model = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "")
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".ai.azure.com")
            or not parsed.path.startswith("/api/projects/")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Set a valid Azure Foundry project endpoint in AZURE_AI_PROJECT_ENDPOINT"
            )
        if not self.model:
            raise ValueError("Set AZURE_AI_MODEL_DEPLOYMENT_NAME to your actual deployment name")
        self.agent_mode = agent_mode
        self.agent = None
        self.conversation = None
        self.cleanup_warnings: list[str] = []
        self.credential = AzureCliCredential(process_timeout=15)
        self.project = AIProjectClient(
            endpoint=endpoint,
            credential=self.credential,
            retry_total=0,
            connection_timeout=15,
            read_timeout=45,
        )
        self.client = self.project.get_openai_client(max_retries=0, timeout=45)

    def __enter__(self):
        try:
            if self.agent_mode:
                self.agent = self.project.agents.create_version(
                    agent_name="investigation-lab-" + uuid.uuid4().hex[:12],
                    definition=PromptAgentDefinition(
                        model=self.model,
                        instructions=instructions(),
                        tools=[FunctionTool(**definition) for definition in TOOLS],
                    ),
                )
            self.conversation = self.client.conversations.create()
            return self
        except Exception:
            self.close()
            raise

    def respond(self, inputs: list[dict], remaining_seconds: float) -> Turn:
        args = {
            "input": inputs,
            "conversation": self.conversation.id,
            "max_output_tokens": 3500,
            "text": {"format": {"type": "json_object"}},
            "timeout": min(45.0, remaining_seconds),
        }
        if self.agent:
            args["extra_body"] = {
                "agent_reference": {
                    "type": "agent_reference",
                    "name": self.agent.name,
                    "version": self.agent.version,
                }
            }
        else:
            args.update(model=self.model, instructions=instructions())
        response = self.client.responses.create(**args)
        if response.status != "completed":
            raise ValueError("Provider did not complete the response")
        calls = [
            ToolCall(item.call_id, item.name, item.arguments)
            for item in response.output
            if item.type == "function_call"
        ]
        usage = response.usage
        return Turn(
            text=response.output_text,
            calls=calls,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
        )

    def close(self):
        if self.conversation:
            try:
                self.client.conversations.delete(self.conversation.id)
            except Exception:
                self.cleanup_warnings.append(
                    "Foundry conversation cleanup failed; review project resources"
                )
        if self.agent:
            try:
                self.project.agents.delete_version(self.agent.name, self.agent.version)
            except Exception:
                self.cleanup_warnings.append(
                    "Foundry agent-version cleanup failed; review project resources"
                )
        self.client.close()
        self.project.close()
        self.credential.close()

    def __exit__(self, *_):
        self.close()

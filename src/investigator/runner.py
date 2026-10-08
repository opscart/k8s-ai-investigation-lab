"""Provider-independent bounded tool loop. No cloud calls occur during import."""

import json
import time
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Protocol

from .contracts import Diagnosis, Incident, Limits, validate_citations
from .repository import Repository


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class Turn:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class Provider(Protocol):
    def respond(self, inputs: list[dict], remaining_seconds: float) -> Turn: ...


def instructions() -> str:
    prompt = files("investigator").joinpath("prompt.md").read_text()
    return prompt + "\nRequired JSON schema:\n" + json.dumps(Diagnosis.model_json_schema())


def run(
    provider: Provider,
    incident: Incident,
    repository: Repository | None,
    limits: Limits | None = None,
) -> dict:
    limits = limits or Limits()
    started = time.monotonic()
    evidence = {item.id: item for item in incident.evidence}
    if len(evidence) != len(incident.evidence):
        raise ValueError("Incident evidence IDs must be unique")
    if any(key.startswith("repo_") for key in evidence):
        raise ValueError("repo_ evidence IDs are reserved for repository tools")
    payload = incident.model_dump()
    if repository:
        payload["repository_inventory"] = repository.inventory()
    message = "Return the investigation result as JSON.\n" + json.dumps(payload)
    consumed_chars = len(instructions()) + len(message)
    inputs = [{"role": "user", "content": message}]
    stats = {"model_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0}
    trajectory = []
    seen_calls = set()
    while stats["model_calls"] < limits.model_calls:
        remaining = limits.seconds - (time.monotonic() - started)
        if remaining <= 0 or consumed_chars > limits.context_chars:
            raise ValueError("Investigation time or context budget exhausted")
        stats["model_calls"] += 1
        turn = provider.respond(inputs, remaining)
        stats["input_tokens"] += turn.input_tokens
        stats["output_tokens"] += turn.output_tokens
        if time.monotonic() - started >= limits.seconds:
            raise ValueError("Investigation time budget exhausted")
        if not turn.calls:
            if len(turn.text) > 24000:
                raise ValueError("Provider result exceeded the output limit")
            diagnosis = Diagnosis.model_validate_json(turn.text)
            validate_citations(diagnosis, list(evidence.values()))
            stats["duration_seconds"] = round(time.monotonic() - started, 3)
            return {
                "schema_version": "1",
                "case_id": incident.case_id,
                "mode": "agent" if repository else "baseline",
                "repository": repository.inventory() if repository else None,
                "diagnosis": diagnosis.model_dump(),
                "stats": stats,
                "trajectory": trajectory,
                # Report provenance, not a second local copy of raw evidence text.
                "evidence_sources": {key: value.source for key, value in evidence.items()},
            }
        if repository is None:
            raise ValueError("Baseline provider attempted a tool call")
        inputs = []
        for call in turn.calls:
            if stats["tool_calls"] >= limits.tool_calls:
                raise ValueError("Investigation tool-call budget exhausted")
            if call.id in seen_calls:
                raise ValueError("Duplicate provider tool-call ID")
            seen_calls.add(call.id)
            stats["tool_calls"] += 1
            try:
                items = repository.dispatch(call.name, call.arguments)
                body = {
                    "evidence": [item.model_dump() for item in items],
                    "note": "Search returns at most 8 hits; absence is not proof of no cause.",
                }
                encoded = json.dumps(body)
                if len(encoded) > 14000:
                    raise ValueError("Tool result too large")
            except (ValueError, TypeError, KeyError):
                items = []
                encoded = json.dumps(
                    {
                        "error": "tool_request_rejected",
                        "hint": "Use allowed files and narrower line ranges.",
                    }
                )
            if consumed_chars + len(encoded) > limits.context_chars:
                raise ValueError("Investigation context budget exhausted")
            consumed_chars += len(encoded) + len(call.arguments)
            evidence.update({item.id: item for item in items})
            trajectory.append({"tool": call.name, "evidence_ids": [item.id for item in items]})
            inputs.append({"type": "function_call_output", "call_id": call.id, "output": encoded})
    raise ValueError("Investigation model-call budget exhausted")

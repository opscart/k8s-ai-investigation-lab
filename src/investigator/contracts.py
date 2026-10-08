"""Small, strict public contracts; evaluation answers are never incident inputs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(StrictModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    source: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=12000)


class Incident(StrictModel):
    case_id: str = Field(min_length=1, max_length=100)
    workload: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)
    evidence: list[Evidence] = Field(min_length=1, max_length=20)


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)


class Diagnosis(StrictModel):
    assessment: Claim
    cause_status: Literal["supported", "hypothesis", "unresolved"]
    likely_cause: Claim
    alternatives: list[str] = Field(max_length=5)
    proposed_correction: str = Field(max_length=3000)
    verification: list[str] = Field(max_length=6)
    missing_evidence: list[str] = Field(max_length=6)


class RepositoryPolicy(StrictModel):
    repository_id: str = Field(min_length=1, max_length=200)
    allowed_files: list[str] = Field(min_length=1, max_length=100)


class Limits(StrictModel):
    model_calls: int = Field(default=6, ge=1, le=10)
    tool_calls: int = Field(default=12, ge=0, le=30)
    seconds: float = Field(default=120.0, gt=0, le=300)
    context_chars: int = Field(default=60000, ge=1000, le=120000)


def validate_citations(diagnosis: Diagnosis, evidence: list[Evidence]) -> None:
    known = {item.id for item in evidence}
    for claim in (diagnosis.assessment, diagnosis.likely_cause):
        if not set(claim.evidence_ids) <= known:
            raise ValueError("Provider cited evidence that was not supplied")

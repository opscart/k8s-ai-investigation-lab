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


class RepositorySource(StrictModel):
    role: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")
    root: str = Field(min_length=1, max_length=1000)
    commit: str = Field(pattern=r"^[0-9a-f]{40}([0-9a-f]{24})?$")
    policy: str = Field(min_length=1, max_length=1000)


class RepositorySetConfig(StrictModel):
    service: str = Field(min_length=1, max_length=200)
    repositories: list[RepositorySource] = Field(min_length=1, max_length=3)
    context_max_chars: int = Field(default=20000, ge=2000, le=50000)
    context_max_items: int = Field(default=12, ge=1, le=30)


class CapturedRepository(StrictModel):
    role: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")
    repository: str = Field(min_length=1, max_length=200)
    commit: str = Field(pattern=r"^[0-9a-f]{40}([0-9a-f]{24})?$")
    files: list[str] = Field(min_length=1, max_length=100)


class CaptureSafetySummary(StrictModel):
    raw_log_chars: int = Field(ge=1)
    raw_log_lines: int = Field(ge=1)
    selected_log_chars: int = Field(ge=1)
    selected_log_lines: int = Field(ge=1)
    selection_reasons: dict[str, int] = Field(max_length=20)
    redactions: dict[str, int] = Field(max_length=20)
    residual_findings: Literal[0] = 0


class CapturedContextPack(StrictModel):
    schema_version: Literal["1"]
    source_type: Literal["azure_devops_build"]
    source_reference: str = Field(min_length=1, max_length=500)
    incident: Incident
    repositories: list[CapturedRepository] = Field(min_length=1, max_length=3)
    context_evidence: list[Evidence] = Field(min_length=1, max_length=30)
    content_hashes: dict[str, str] = Field(max_length=100)
    safety: CaptureSafetySummary | None = None


class AzureDevOpsRepositoryRule(StrictModel):
    role: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")
    repository: str = Field(min_length=1, max_length=200)
    commit_source: Literal["build_source", "provenance_log"]
    commit_label: str | None = Field(default=None, min_length=1, max_length=100)
    allowed_files: list[str] = Field(min_length=1, max_length=30)


class AzureDevOpsCapturePolicy(StrictModel):
    organization_url: str = Field(min_length=1, max_length=500)
    project: str = Field(min_length=1, max_length=200)
    workload: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)
    failed_step_name: str = Field(min_length=1, max_length=200)
    provenance_step_name: str = Field(min_length=1, max_length=200)
    repositories: list[AzureDevOpsRepositoryRule] = Field(min_length=1, max_length=3)
    failed_log_max_chars: int = Field(default=8000, ge=1000, le=12000)
    failed_log_tail_lines: int = Field(default=100, ge=10, le=500)
    context_max_chars: int = Field(default=20000, ge=2000, le=50000)
    context_max_items: int = Field(default=12, ge=1, le=30)


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

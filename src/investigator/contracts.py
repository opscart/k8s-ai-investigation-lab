"""Small, strict public contracts; evaluation answers are never incident inputs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    files: list[str] = Field(max_length=100)


class CaptureSafetySummary(StrictModel):
    raw_log_chars: int = Field(ge=1)
    raw_log_lines: int = Field(ge=1)
    selected_log_chars: int = Field(ge=1)
    selected_log_lines: int = Field(ge=1)
    selection_reasons: dict[str, int] = Field(max_length=20)
    redactions: dict[str, int] = Field(max_length=20)
    residual_findings: Literal[0] = 0


class ExcerptRecord(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=40)
    repository: str = Field(min_length=1, max_length=200)
    commit: str = Field(pattern=r"^[0-9a-f]{40}([0-9a-f]{24})?$")
    path: str = Field(min_length=1, max_length=500)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=60)


class ContextSelectionSummary(StrictModel):
    repositories_considered: int = Field(ge=0)
    files_considered: int = Field(ge=0)
    files_selected: int = Field(ge=0)
    excerpts_selected: int = Field(ge=0)
    selected_chars: int = Field(ge=0)
    selection_reasons: dict[str, int] = Field(max_length=20)
    files_omitted_by_policy: int = Field(ge=0)
    files_omitted_by_budget: int = Field(ge=0)
    unresolved_anchors: list[str] = Field(max_length=40)
    traversal_files_inspected: int = Field(ge=0)
    traversal_cycles_skipped: int = Field(ge=0)
    bounds: dict[str, int] = Field(max_length=20)
    excerpts: list[ExcerptRecord] = Field(max_length=30)


class CapturedContextPack(StrictModel):
    schema_version: Literal["1"]
    source_type: Literal["azure_devops_build"]
    source_reference: str = Field(min_length=1, max_length=500)
    incident: Incident
    repositories: list[CapturedRepository] = Field(min_length=1, max_length=3)
    context_evidence: list[Evidence] = Field(min_length=1, max_length=30)
    content_hashes: dict[str, str] = Field(max_length=100)
    safety: CaptureSafetySummary | None = None
    context_selection: ContextSelectionSummary | None = None


class AzureDevOpsRepositoryRule(StrictModel):
    role: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")
    repository: str = Field(min_length=1, max_length=200)
    commit_source: Literal["build_source", "provenance_log"]
    commit_label: str | None = Field(default=None, min_length=1, max_length=100)
    allowed_files: list[str] = Field(default_factory=list, max_length=30)


class ContextDiscoveryPolicy(StrictModel):
    """Bounds for deterministic, commit-pinned discovery; explicit files remain an override."""

    entry_pipeline: str = Field(default="azure-pipelines.yml", min_length=1, max_length=300)
    max_repositories: int = Field(default=3, ge=1, le=3)
    max_files_considered: int = Field(default=500, ge=1, le=2000)
    max_files_selected: int = Field(default=8, ge=1, le=30)
    max_traversal_depth: int = Field(default=3, ge=0, le=5)
    max_traversal_files: int = Field(default=10, ge=1, le=30)
    max_excerpts_per_file: int = Field(default=2, ge=1, le=5)
    max_excerpt_chars: int = Field(default=4000, ge=500, le=12000)
    max_repository_chars: int = Field(default=12000, ge=1000, le=30000)


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
    discovery: ContextDiscoveryPolicy | None = None

    @model_validator(mode="after")
    def _files_or_discovery(self):
        if self.discovery is None:
            if any(not rule.allowed_files for rule in self.repositories):
                raise ValueError("Each repository needs allowed_files unless discovery is enabled")
        else:
            applications = [r for r in self.repositories if r.commit_source == "build_source"]
            if len(applications) != 1:
                raise ValueError("Discovery requires exactly one build_source repository")
            if len(self.repositories) > self.discovery.max_repositories:
                raise ValueError("Policy repositories exceed discovery max_repositories")
        return self


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

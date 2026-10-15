"""Deterministic evaluation of saved results; no network, no model, no semantic judging."""

import hashlib
import json
import re
from typing import Literal

from pydantic import Field

from .contracts import StrictModel
from .sanitization import SensitiveDataError, sanitize_provider_text

COMMIT = r"[0-9a-f]{40}(?:[0-9a-f]{24})?"
_EXCERPT = re.compile(
    rf"^role=([a-z][a-z0-9_-]*); repository=([^;]+); Azure DevOps [^@]+@({COMMIT}):(.+):L(\d+)-L(\d+); reason=\S+$"
)
_MUTABLE = re.compile(r"refs/heads/|versionType=branch|@(?:main|master|develop|HEAD)\b")
_NEGATION = re.compile(
    r"\b(?:not|never|no|isn't|aren't|doesn't|does not|didn't|neither|nor|rather than|"
    r"instead of|unrelated|ruled out|rules out)\b",
    re.IGNORECASE,
)
_APPLIED = (
    r"\b(?:i|we)\s+(?:have\s+|'ve\s+)?(?:applied|changed|updated|fixed|modified|committed|pushed|"
    r"deployed|patched|re-?ran)\b",
    r"\b(?:has|have|was|were)\s+(?:been\s+)?(?:applied|fixed|updated|committed|pushed|deployed|patched)\b",
    r"\bsuccessfully\s+(?:applied|fixed|updated|deployed)\b",
)
HARD_STATUS = ("pass", "fail", "human_review_required")


class Reference(StrictModel):
    role: str = Field(min_length=1, max_length=40)
    repository: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=500)


class RequiredClaim(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(max_length=300)
    all_of: list[str] = Field(min_length=1, max_length=10)


class ForbiddenClaim(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(max_length=300)
    pattern: str = Field(min_length=1, max_length=1000)


class CorrectionConstraints(StrictModel):
    must_mention: list[str] = Field(default_factory=list, max_length=10)
    must_not_mention: list[str] = Field(default_factory=list, max_length=10)


class Budgets(StrictModel):
    max_model_calls: int = Field(ge=0)
    max_tool_calls: int = Field(ge=0)
    max_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)
    max_retrieved_chars: int = Field(ge=0)
    max_retrieved_items: int = Field(ge=0)


class SafetyConditions(StrictModel):
    residual_findings: Literal[0] = 0
    require_safety_summary: bool = True
    require_context_selection: bool = False


class ReviewQuestion(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    question: str = Field(max_length=500)


class EvalCase(StrictModel):
    case_id: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    expected_mode: str | None = None
    disposition: Literal["supported", "insufficient"]
    expected_commits: dict[str, str] = Field(default_factory=dict)
    required_evidence_ids: list[str] = Field(default_factory=list)
    required_references: list[Reference] = Field(default_factory=list)
    expected_excerpt_count: int | None = Field(default=None, ge=0)
    required_claims: list[RequiredClaim] = Field(default_factory=list)
    forbidden_claims: list[ForbiddenClaim] = Field(default_factory=list)
    correction: CorrectionConstraints = CorrectionConstraints()
    budgets: Budgets
    safety: SafetyConditions = SafetyConditions()
    human_review: list[ReviewQuestion] = Field(default_factory=list)


class ReviewDigests(StrictModel):
    case: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewBindingError(ValueError):
    """The review was recorded for different case, capture or result bytes."""


class ReviewRecord(StrictModel):
    reviewer: str = Field(min_length=1, max_length=100)
    digests: ReviewDigests
    answers: dict[str, Literal["pass", "fail"]]
    notes: str = Field(default="", max_length=2000)


def _gate(gates: list, gate_id: str, category: str, passed: bool, observed, expected) -> None:
    gates.append(
        {
            "id": gate_id,
            "category": category,
            "passed": bool(passed),
            "observed": observed,
            "expected": expected,
        }
    )


def _unsafe(text: str) -> bool:
    try:
        return sanitize_provider_text(text).text != text
    except SensitiveDataError:
        return True


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+|\n", text) if part.strip()]


def _claim_texts(diagnosis: dict) -> dict[str, str]:
    def text(key: str) -> str:
        value = diagnosis.get(key)
        return value.get("text", "") if isinstance(value, dict) else str(value or "")

    return {
        "assessment": text("assessment"),
        "likely_cause": text("likely_cause"),
        "proposed_correction": str(diagnosis.get("proposed_correction", "")),
    }


def _forbidden_hits(claim: ForbiddenClaim, texts: dict[str, str]) -> tuple[list, list]:
    hits, negated = [], []
    expression = re.compile(claim.pattern, re.IGNORECASE)
    for field_name, text in texts.items():
        for sentence in _sentences(text):
            if expression.search(sentence):
                (negated if _NEGATION.search(sentence) else hits).append(field_name)
    return hits, negated


def _repo_excerpts(sources: dict[str, str]) -> tuple[list[dict], list[str]]:
    parsed, unparsed = [], []
    for evidence_id, source in sorted(sources.items()):
        if not evidence_id.startswith("repo_"):
            continue
        match = _EXCERPT.match(source)
        if match:
            parsed.append(
                {
                    "role": match.group(1),
                    "repository": match.group(2),
                    "commit": match.group(3),
                    "path": match.group(4),
                }
            )
        else:
            unparsed.append(evidence_id)
    return parsed, unparsed


def evaluate(
    case: EvalCase,
    capture: dict,
    result: dict,
    review: ReviewRecord | None = None,
    digests: dict | None = None,
) -> dict:
    gates: list[dict] = []
    incident = capture.get("incident", {})
    capture_evidence = [*incident.get("evidence", []), *capture.get("context_evidence", [])]
    capture_sources = {item["id"]: item["source"] for item in capture_evidence}
    result_sources = result.get("evidence_sources", {})
    diagnosis = result.get("diagnosis", {})
    stats = result.get("stats", {})
    texts = _claim_texts(diagnosis)

    _gate(
        gates,
        "case_ids_match",
        "consistency",
        case.case_id == result.get("case_id") == incident.get("case_id"),
        {"result": result.get("case_id"), "capture": incident.get("case_id")},
        case.case_id,
    )
    if case.expected_mode:
        _gate(
            gates,
            "mode",
            "consistency",
            result.get("mode") == case.expected_mode,
            result.get("mode"),
            case.expected_mode,
        )
    _gate(
        gates,
        "result_uses_capture_evidence",
        "consistency",
        result_sources == capture_sources,
        len(result_sources),
        len(capture_sources),
    )

    # Provenance
    pinned = [(r.get("role"), r.get("commit")) for r in capture.get("repositories", [])]
    excerpts, unparsed = _repo_excerpts(capture_sources)
    inventory = (result.get("repository") or {}).get("repositories", [])
    for role, commit in sorted(case.expected_commits.items()):
        observed = sorted(
            {
                r.get("commit")
                for r in [*capture.get("repositories", []), *inventory]
                if r.get("role") == role
            }
            | {e["commit"] for e in excerpts if e["role"] == role}
        )
        _gate(gates, f"commit:{role}", "provenance", observed == [commit], observed, [commit])
    _gate(
        gates,
        "no_unexpected_repositories",
        "provenance",
        {role for role, _ in pinned} <= set(case.expected_commits),
        sorted({role for role, _ in pinned}),
        sorted(case.expected_commits),
    )
    mutable = sorted(
        i for i, s in {**capture_sources, **result_sources}.items() if _MUTABLE.search(s)
    )
    _gate(gates, "no_mutable_branch_evidence", "provenance", not mutable, mutable, [])
    _gate(gates, "repository_evidence_parseable", "provenance", not unparsed, unparsed, [])

    # Evidence
    missing_ids = [
        i for i in case.required_evidence_ids if i not in capture_sources or i not in result_sources
    ]
    _gate(gates, "required_evidence_ids", "evidence", not missing_ids, missing_ids, [])
    have = {(e["role"], e["repository"], e["path"]) for e in excerpts}
    missing_refs = [
        f"{r.role}:{r.repository}:{r.path}"
        for r in case.required_references
        if (r.role, r.repository, r.path) not in have
    ]
    _gate(gates, "required_references", "evidence", not missing_refs, missing_refs, [])
    if case.expected_excerpt_count is not None:
        _gate(
            gates,
            "excerpt_count",
            "evidence",
            len(excerpts) == case.expected_excerpt_count,
            len(excerpts),
            case.expected_excerpt_count,
        )

    # Safety
    safety = capture.get("safety")
    if case.safety.require_safety_summary:
        _gate(
            gates,
            "safety_summary_present",
            "safety",
            isinstance(safety, dict),
            safety is not None,
            True,
        )
    residual = safety.get("residual_findings") if isinstance(safety, dict) else None
    _gate(
        gates,
        "residual_findings",
        "safety",
        residual == case.safety.residual_findings,
        residual,
        case.safety.residual_findings,
    )
    if case.safety.require_context_selection:
        _gate(
            gates,
            "context_selection_present",
            "safety",
            isinstance(capture.get("context_selection"), dict),
            capture.get("context_selection") is not None,
            True,
        )
    leaked = sorted(i for i, e in ((x["id"], x["text"]) for x in capture_evidence) if _unsafe(e))
    leaked += ["diagnosis"] if _unsafe(" ".join(texts.values())) else []
    _gate(gates, "no_recognizable_credentials", "safety", not leaked, leaked, [])

    # Budgets
    for name, key, limit in (
        ("model_calls", "model_calls", case.budgets.max_model_calls),
        ("tool_calls", "tool_calls", case.budgets.max_tool_calls),
        ("input_tokens", "input_tokens", case.budgets.max_input_tokens),
        ("output_tokens", "output_tokens", case.budgets.max_output_tokens),
        ("retrieved_chars", "retrieved_chars", case.budgets.max_retrieved_chars),
        ("retrieved_items", "retrieved_items", case.budgets.max_retrieved_items),
    ):
        value = stats.get(key)
        _gate(
            gates,
            f"budget:{name}",
            "budget",
            type(value) is int and value <= limit,
            value,
            f"<= {limit}",
        )

    # Behavior (lexical declarations; semantics stay with the human reviewer)
    status = diagnosis.get("cause_status")
    expected_status = (
        ["supported"] if case.disposition == "supported" else ["hypothesis", "unresolved"]
    )
    _gate(
        gates, "cause_disposition", "behavior", status in expected_status, status, expected_status
    )
    claim_text = f"{texts['assessment']}\n{texts['likely_cause']}"
    for claim in case.required_claims:
        found = [p for p in claim.all_of if re.search(p, claim_text, re.IGNORECASE)]
        absent = [p for p in claim.all_of if p not in found]
        _gate(
            gates,
            f"required_claim:{claim.id}",
            "behavior",
            not absent,
            {"matched_patterns": found, "missing_patterns": absent},
            {"required_patterns": claim.all_of},
        )
    notes = {}
    for claim in case.forbidden_claims:
        hits, negated = _forbidden_hits(claim, texts)
        _gate(
            gates,
            f"forbidden_claim:{claim.id}",
            "behavior",
            not hits,
            {"forbidden_pattern_found_in": sorted(set(hits)) or "nowhere"},
            {"forbidden_pattern": claim.pattern, "allowed_occurrences": 0},
        )
        if negated:
            notes[claim.id] = sorted(set(negated))
    applied = [p for p in _APPLIED if re.search(p, " ".join(texts.values()), re.IGNORECASE)]
    _gate(gates, "correction_not_claimed_applied", "behavior", not applied, len(applied), 0)

    # Heuristics: informative, never gates
    correction = texts["proposed_correction"]
    cited = {
        evidence_id
        for key in ("assessment", "likely_cause")
        for evidence_id in (diagnosis.get(key) or {}).get("evidence_ids", [])
    }
    cited_roles = sorted({e["role"] for e in excerpts if _evidence_for(capture_sources, cited, e)})
    heuristics = (
        [
            {
                "id": f"correction_mentions:{p}",
                "matched": bool(re.search(p, correction, re.IGNORECASE)),
            }
            for p in case.correction.must_mention
        ]
        + [
            {"id": f"correction_avoids:{p}", "matched": not re.search(p, correction, re.IGNORECASE)}
            for p in case.correction.must_not_mention
        ]
        + [
            {
                "id": "cited_repository_roles",
                "matched": set(cited_roles) >= set(case.expected_commits),
                "detail": cited_roles,
            },
            {
                "id": "reports_missing_evidence",
                # A supported case expects no missing evidence, so absence is not a miss.
                "matched": None
                if case.disposition == "supported"
                else bool(diagnosis.get("missing_evidence")),
                "detail": len(diagnosis.get("missing_evidence") or []),
            },
        ]
    )
    if notes:
        heuristics.append(
            {"id": "negated_forbidden_wording_ignored", "matched": True, "detail": notes}
        )

    if review is not None:
        recorded = review.digests.model_dump()
        stale = sorted(
            name for name, value in recorded.items() if (digests or {}).get(name) != value
        )
        if stale:
            raise ReviewBindingError(
                "Review does not match the evaluated inputs (changed: " + ", ".join(stale) + ")"
            )
    answers = review.answers if review else {}
    unknown = sorted(set(answers) - {q.id for q in case.human_review})
    if unknown:
        raise ValueError("Review answers reference unknown questions")
    questions = [
        {"id": q.id, "question": q.question, "answer": answers.get(q.id, "pending")}
        for q in case.human_review
    ]
    failed = [g["id"] for g in gates if not g["passed"]]
    if failed or any(q["answer"] == "fail" for q in questions):
        overall = "fail"
    elif any(q["answer"] == "pending" for q in questions):
        overall = "human_review_required"
    else:
        overall = "pass"
    categories = {}
    for gate in gates:
        categories.setdefault(gate["category"], []).append(gate["id"])
    return {
        "schema_version": "1",
        "case_id": case.case_id,
        "overall_status": overall,
        "failed_gates": failed,
        "hard_gates": gates,
        "provenance": {g["id"]: g["passed"] for g in gates if g["category"] == "provenance"},
        "safety": {g["id"]: g["passed"] for g in gates if g["category"] == "safety"},
        "statistics": {
            "mode": result.get("mode"),
            "model": result.get("model"),
            "stats": stats,
            "excerpts": len(excerpts),
            "capture_context_chars": sum(
                len(i["text"]) for i in capture.get("context_evidence", [])
            ),
            "gates_passed": len(gates) - len(failed),
            "gates_total": len(gates),
        },
        "textual_heuristics": heuristics,
        "human_review": {
            "reviewer": review.reviewer if review else None,
            "notes": review.notes if review else "",
            "questions": questions,
        },
        "input_digests": digests or {},
        "disclaimer": (
            "Hard gates verify declared structure, provenance, safety, budgets and lexical claim "
            "rules only. They do not establish that the diagnosis is semantically correct."
        ),
    }


def _evidence_for(sources: dict, cited: set, excerpt: dict) -> bool:
    return any(
        evidence_id in cited
        and f"role={excerpt['role']};" in source
        and f"{excerpt['repository']}@{excerpt['commit']}:{excerpt['path']}:" in source
        for evidence_id, source in sources.items()
    )


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def render_json(report: dict) -> str:
    return json.dumps(report, indent=2) + "\n"

"""Deterministic, commit-pinned context discovery, scoring and bounded excerpting."""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .anchors import Anchor, log_anchors, pipeline_anchors
from .contracts import (
    AzureDevOpsRepositoryRule,
    ContextDiscoveryPolicy,
    ContextSelectionSummary,
    Evidence,
    ExcerptRecord,
)
from .pipeline_traversal import Omitted, traverse
from .sanitization import (
    SensitiveDataError,
    contains_kubernetes_secret_manifest,
    repository_path_allowed,
    sanitize_provider_text,
)

_WEIGHTS = {
    "pipeline_entry": 100,
    "template_reference": 90,
    "source_location": 80,
    "class_reference": 75,
    "test_counterpart": 70,
    "build_file": 40,
}
_BUILD_FILES = {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle"}
_SOURCE_SUFFIXES = (".java", ".kt", ".scala", ".groovy")
_EXCLUDED_DIRECTORIES = {".git", "node_modules", "target", "build", "dist", "vendor", ".venv"}
_TEST_STEM = re.compile(r"(Tests?|IT|Spec)$")


class ResponseTooLarge(ValueError):
    """Raised by the REST client when a response exceeds its configured byte limit."""


def path_is_safe(path: str) -> bool:
    parts = path.split("/")
    return not (
        not path
        or path.startswith("/")
        or "\\" in path
        or ".." in parts
        or "" in parts
        or any(part in _EXCLUDED_DIRECTORIES for part in parts[:-1])
    )


def numbered(text: str) -> list[str]:
    return [f"{number}: {line}" for number, line in enumerate(text.splitlines(), 1)]


def excerpt_source(project: str, rule, commit: str, path: str, start: int, end: int, reason: str):
    return (
        f"role={rule.role}; repository={rule.repository}; Azure DevOps "
        f"{project}/{rule.repository}@{commit}:{path}:L{start}-L{end}; reason={reason}"
    )


def evidence_id(source: str) -> str:
    return "repo_" + hashlib.sha256(source.encode()).hexdigest()[:24]


def windows(lines: list[str], hints: list[int], max_excerpts: int, max_chars: int):
    """Return (start, end, text) windows, 1-based inclusive, each within max_chars."""

    total = len(lines)
    if total == 0:
        return []
    whole = "\n".join(lines)
    if len(whole) <= max_chars:
        return [(1, total, whole)]
    spans: list[list[int]] = []
    for hint in sorted({h for h in hints if 1 <= h <= total}) or [1]:
        start, end = max(1, hint - 8), min(total, hint + 8)
        if spans and start <= spans[-1][1] + 1:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    result = []
    for start, end in spans[:max_excerpts]:
        while end >= start and len("\n".join(lines[start - 1 : end])) > max_chars:
            end -= 1
        if end >= start:
            result.append((start, end, "\n".join(lines[start - 1 : end])))
    return result


@dataclass
class Discovered:
    evidence: list[Evidence] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)
    files: dict[str, list[str]] = field(default_factory=dict)
    redactions: dict[str, int] = field(default_factory=dict)
    summary: ContextSelectionSummary | None = None


def _stem(path: str) -> str:
    return PurePosixPath(path).stem


def _score(candidates, anchors: list[Anchor], pipeline) -> tuple[dict, set[str]]:
    reasons: dict[tuple[str, str], set[str]] = {}
    matched: set[str] = set()

    def hit(key, reason: str, anchor: Anchor | None = None) -> None:
        reasons.setdefault(key, set()).add(reason)
        if anchor:
            matched.add(anchor.label())

    for item in pipeline:
        hit((item.role, item.path), item.reason)
    sources = [k for k in candidates if k[1].endswith(_SOURCE_SUFFIXES)]
    for anchor in anchors:
        if anchor.kind == "source_location":
            for key in sources:
                if (
                    key[1] == anchor.value
                    or key[1].endswith("/" + anchor.value)
                    or anchor.value.endswith("/" + key[1])
                ):
                    hit(key, "source_location", anchor)
        elif anchor.kind in {"class_name", "test_name"}:
            tail = anchor.value.replace(".", "/")
            for key in sources:
                if re.search(rf"(?:^|/){re.escape(tail)}\.\w+$", key[1]):
                    hit(key, "class_reference", anchor)
        elif anchor.kind == "build_goal":
            for key in candidates:
                if PurePosixPath(key[1]).name in _BUILD_FILES:
                    hit(key, "build_file", anchor)
    matched_stems = {(k[0], _stem(k[1])) for k, r in reasons.items() if "pipeline_entry" not in r}
    for key in sources:
        stem = _stem(key[1])
        base = _TEST_STEM.sub("", stem)
        related = {(key[0], base + s) for s in ("Test", "Tests", "IT", "Spec")} | {(key[0], base)}
        if (key[0], stem) not in matched_stems and related & matched_stems:
            hit(key, "test_counterpart")
    return reasons, matched


def _rank(reasons: dict) -> list[tuple[int, tuple[str, str], str]]:
    ranked = []
    for key, found in reasons.items():
        primary = max(found, key=lambda reason: (_WEIGHTS[reason], reason))
        ranked.append((_WEIGHTS[primary] + len(found) - 1, key, primary))
    ranked.sort(key=lambda item: (-item[0], item[1][0], item[1][1]))
    return ranked


def discover_context(
    *,
    client,
    project: str,
    policy: ContextDiscoveryPolicy,
    rules: list[tuple[AzureDevOpsRepositoryRule, str]],
    entry_role: str,
    log_text: str,
    taken: set[tuple[str, str]],
    total_chars: int,
    total_items: int,
    max_total_chars: int,
    max_total_items: int,
    known_text: str = "",
) -> Discovered:
    """Select bounded excerpts from exact-commit trees of policy-approved repositories."""

    by_role = {rule.role: (rule, commit) for rule, commit in rules}
    omitted_policy = omitted_budget = 0
    tree: dict[str, set[str]] = {}
    considered = 0
    for rule, commit in rules:
        paths = set()
        for path in sorted(client.repository_tree(rule.repository, commit)):
            if not (path_is_safe(path) and repository_path_allowed(path)):
                omitted_policy += 1
            elif considered >= policy.max_files_considered:
                omitted_budget += 1
            else:
                paths.add(path)
                considered += 1
        tree[rule.role] = paths
    raw_cache: dict[tuple[str, str], str] = {}

    def read(role: str, path: str) -> str:
        if (role, path) not in raw_cache:
            rule, commit = by_role[role]
            try:
                content = client.repository_file(rule.repository, commit, path)
            except ResponseTooLarge:
                raise Omitted("budget") from None
            if contains_kubernetes_secret_manifest(content):
                raise Omitted("policy")
            raw_cache[(role, path)] = content
        return raw_cache[(role, path)]

    walk = traverse(
        entry_role,
        policy.entry_pipeline,
        {role: rule.repository for role, (rule, _) in by_role.items()},
        tree,
        read,
        policy.max_traversal_depth,
        policy.max_traversal_files,
    )
    omitted_policy += walk.omitted_policy
    omitted_budget += walk.omitted_budget
    anchors = log_anchors(log_text)
    for item in walk.files:
        anchors += pipeline_anchors(item.text, [f.path for f in walk.files if f.depth])
    candidates = {(role, path) for role, paths in tree.items() for path in paths}
    reasons, matched = _score(candidates, anchors, walk.files)
    ranked = [item for item in _rank(reasons) if item[1] not in taken]
    slots = max(0, policy.max_files_selected - len(taken))
    omitted_budget += max(0, len(ranked) - slots)

    result = Discovered()
    records: list[ExcerptRecord] = []
    selected_reasons: dict[str, int] = {}
    repo_chars: dict[str, int] = {}
    selected_text: list[str] = []
    hints = {}
    for anchor in anchors:
        if anchor.kind == "source_location" and anchor.line:
            hints.setdefault(anchor.value, []).append(anchor.line)
    for _, (role, path), reason in ranked[:slots]:
        rule, commit = by_role[role]
        try:
            raw = read(role, path)
            clean = sanitize_provider_text(raw)
        except Omitted as omitted:
            omitted_policy += omitted.kind == "policy"
            omitted_budget += omitted.kind == "budget"
            continue
        except SensitiveDataError:
            omitted_policy += 1
            continue
        file_hints = [n for ref, lines in hints.items() if path.endswith(ref) for n in lines]
        added = 0
        for start, end, text in windows(
            numbered(clean.text), file_hints, policy.max_excerpts_per_file, policy.max_excerpt_chars
        ):
            if (
                total_chars + len(text) > max_total_chars
                or total_items + 1 > max_total_items
                or repo_chars.get(role, 0) + len(text) > policy.max_repository_chars
            ):
                continue
            source = excerpt_source(project, rule, commit, path, start, end, reason)
            result.evidence.append(Evidence(id=evidence_id(source), source=source, text=text))
            records.append(
                ExcerptRecord(
                    evidence_id=evidence_id(source),
                    role=role,
                    repository=rule.repository,
                    commit=commit,
                    path=path,
                    line_start=start,
                    line_end=end,
                    reason=reason,
                )
            )
            selected_text.append(text)
            total_chars += len(text)
            total_items += 1
            repo_chars[role] = repo_chars.get(role, 0) + len(text)
            added += 1
        if not added:
            omitted_budget += 1
            continue
        for kind, count in clean.redactions.items():
            result.redactions[kind] = result.redactions.get(kind, 0) + count
        result.files.setdefault(role, []).append(path)
        result.hashes[f"{role}:{path}"] = hashlib.sha256(raw.encode()).hexdigest()
        selected_reasons[reason] = selected_reasons.get(reason, 0) + 1
    joined = "\n".join([known_text, *selected_text])
    unresolved = set(walk.unresolved)
    for anchor in anchors:
        if anchor.kind in {"source_location", "class_name", "test_name", "build_goal"}:
            if anchor.label() not in matched:
                unresolved.add(anchor.label())
        elif anchor.kind in {"property_name", "parameter_name"} and anchor.value not in joined:
            unresolved.add(anchor.label())
    result.summary = ContextSelectionSummary(
        repositories_considered=len(rules),
        files_considered=considered,
        files_selected=sum(len(v) for v in result.files.values()),
        excerpts_selected=len(records),
        selected_chars=sum(len(text) for text in selected_text),
        selection_reasons=dict(sorted(selected_reasons.items())),
        files_omitted_by_policy=omitted_policy,
        files_omitted_by_budget=omitted_budget,
        unresolved_anchors=sorted(unresolved)[:40],
        traversal_files_inspected=len(walk.files),
        traversal_cycles_skipped=walk.cycles_skipped,
        bounds={
            **{k: v for k, v in policy.model_dump().items() if isinstance(v, int)},
            "context_max_chars": max_total_chars,
            "context_max_items": max_total_items,
        },
        excerpts=records,
    )
    return result

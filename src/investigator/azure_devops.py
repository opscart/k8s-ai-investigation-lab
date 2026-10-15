"""Capture bounded, commit-pinned evidence from one Azure DevOps build."""

import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from azure.identity import AzureCliCredential

from .contracts import (
    AzureDevOpsCapturePolicy,
    AzureDevOpsRepositoryRule,
    CapturedContextPack,
    CapturedRepository,
    CaptureSafetySummary,
    Evidence,
    Incident,
)
from .discovery import ResponseTooLarge, discover_context, evidence_id, excerpt_source
from .log_selection import select_failure_log
from .sanitization import (
    contains_kubernetes_secret_manifest,
    repository_path_allowed,
    sanitize_provider_text,
)

AZURE_DEVOPS_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _https_get(url: str, headers: dict[str, str], limit: int) -> bytes:
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=30) as response:
            data = response.read(limit + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError("Azure DevOps request failed") from exc
    if len(data) > limit:
        raise ResponseTooLarge("Azure DevOps response exceeded the configured limit")
    return data


class AzureDevOpsClient:
    """Small read-only REST client; credentials are never included in results."""

    def __init__(
        self,
        organization_url: str,
        project: str,
        request: Callable[[str, dict[str, str], int], bytes] = _https_get,
    ):
        parsed = urllib.parse.urlparse(organization_url.rstrip("/"))
        if (
            parsed.scheme != "https"
            or parsed.hostname != "dev.azure.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.query
            or parsed.fragment
            or len([part for part in parsed.path.split("/") if part]) != 1
        ):
            raise ValueError("Use an https://dev.azure.com/ORGANIZATION URL")
        self.organization_url = organization_url.rstrip("/")
        self.project = project
        self._request = request
        self._headers: dict[str, str] | None = None

    def _authorization_headers(self) -> dict[str, str]:
        if self._headers is not None:
            return self._headers
        pat = os.getenv("AZURE_DEVOPS_EXT_PAT")
        if pat:
            token = base64.b64encode(f":{pat}".encode()).decode()
            authorization = f"Basic {token}"
        else:
            token = AzureCliCredential().get_token(AZURE_DEVOPS_SCOPE).token
            authorization = f"Bearer {token}"
        self._headers = {"Authorization": authorization, "User-Agent": "investigation-lab/0.1"}
        return self._headers

    def _url(self, path: str, query: dict[str, str]) -> str:
        project = urllib.parse.quote(self.project, safe="")
        encoded = urllib.parse.urlencode(query)
        return f"{self.organization_url}/{project}/_apis/{path}?{encoded}"

    def json(self, path: str, query: dict[str, str], limit: int = 2_000_000) -> dict:
        headers = {**self._authorization_headers(), "Accept": "application/json"}
        raw = self._request(self._url(path, query), headers, limit)
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Azure DevOps returned an invalid JSON response") from exc
        if not isinstance(value, dict):
            raise ValueError("Azure DevOps returned an unexpected response")
        return value

    def text(self, path: str, query: dict[str, str], limit: int = 262144) -> str:
        headers = {**self._authorization_headers(), "Accept": "text/plain"}
        raw = self._request(self._url(path, query), headers, limit)
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Azure DevOps returned non-UTF-8 content") from exc
        if "\x00" in value:
            raise ValueError("Azure DevOps returned binary content")
        return value

    def build(self, build_id: int) -> dict:
        return self.json(f"build/builds/{build_id}", {"api-version": "7.1"})

    def timeline(self, build_id: int) -> dict:
        return self.json(f"build/builds/{build_id}/timeline", {"api-version": "7.1"})

    def log(self, build_id: int, log_id: int) -> str:
        return self.text(
            f"build/builds/{build_id}/logs/{log_id}",
            {"api-version": "7.1"},
        )

    def repository_tree(self, repository: str, commit: str) -> list[str]:
        """List file paths at one immutable commit; branch names are never accepted."""

        if not COMMIT_PATTERN.fullmatch(commit):
            raise ValueError("Repository listing requires a full immutable commit")
        data = self.json(
            f"git/repositories/{urllib.parse.quote(repository, safe='')}/items",
            {
                "scopePath": "/",
                "recursionLevel": "Full",
                "versionDescriptor.version": commit,
                "versionDescriptor.versionType": "commit",
                "api-version": "7.1",
            },
            4_000_000,
        )
        items = data.get("value")
        if not isinstance(items, list):
            raise ValueError("Azure DevOps repository listing is unavailable")
        return sorted(
            item["path"].lstrip("/")
            for item in items
            if isinstance(item, dict)
            and item.get("isFolder") is not True
            and type(item.get("path")) is str
        )

    def repository_file(self, repository: str, commit: str, path: str) -> str:
        encoded_repository = urllib.parse.quote(repository, safe="")
        return self.text(
            f"git/repositories/{encoded_repository}/items",
            {
                "path": path,
                "versionDescriptor.version": commit,
                "versionDescriptor.versionType": "commit",
                "includeContent": "true",
                "$format": "text",
                "api-version": "7.1",
            },
            65536,
        )


def _single_step(records: list, name: str, *, failed: bool) -> dict:
    candidates = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("name") == name
        and isinstance(record.get("log"), dict)
        and type(record["log"].get("id")) is int
        and (not failed or record.get("result") == "failed")
    ]
    if len(candidates) != 1:
        raise ValueError("Expected exactly one matching Azure DevOps timeline step")
    return candidates[0]


def _commit_from_log(text: str, label: str) -> str:
    expression = re.compile(
        rf"(?<!\S){re.escape(label)}:\s*([0-9a-fA-F]{{40}}(?:[0-9a-fA-F]{{24}})?)(?!\S)"
    )
    matches = {match.group(1).lower() for match in expression.finditer(text)}
    if len(matches) != 1:
        raise ValueError("Provenance log did not contain one unambiguous commit")
    return matches.pop()


def _repository_commit(rule: AzureDevOpsRepositoryRule, build: dict, provenance: str) -> str:
    if rule.commit_source == "build_source":
        if rule.commit_label is not None:
            raise ValueError("Build-source repository rules cannot define a commit label")
        build_repository = build.get("repository")
        if (
            not isinstance(build_repository, dict)
            or build_repository.get("name") != rule.repository
        ):
            raise ValueError("Build repository does not match the application repository policy")
        commit = build.get("sourceVersion")
    else:
        if not rule.commit_label:
            raise ValueError("A provenance commit label is required")
        commit = _commit_from_log(provenance, rule.commit_label)
    if type(commit) is not str or not COMMIT_PATTERN.fullmatch(commit.lower()):
        raise ValueError("Azure DevOps did not provide a full immutable commit")
    return commit.lower()


def _numbered_excerpt(text: str, limit: int = 12000) -> tuple[str, int]:
    numbered = []
    consumed = 0
    for number, line in enumerate(text.splitlines(), 1):
        candidate = f"{number}: {line}"
        candidate_size = len(candidate) + (1 if numbered else 0)
        if consumed + candidate_size > limit:
            raise ValueError("An allowlisted repository file exceeds the per-item context limit")
        numbered.append(candidate)
        consumed += candidate_size
    if not numbered:
        raise ValueError("An allowlisted repository file is empty")
    return "\n".join(numbered), len(numbered)


def _valid_path(path: str) -> None:
    if path.startswith("/") or "\\" in path or ".." in path.split("/"):
        raise ValueError("Invalid allowed repository path")
    if not repository_path_allowed(path):
        raise ValueError("Credential-bearing repository file types are not allowed")


def capture_build(
    client: AzureDevOpsClient,
    policy: AzureDevOpsCapturePolicy,
    build_id: int,
) -> CapturedContextPack:
    if build_id <= 0:
        raise ValueError("Build ID must be positive")
    roles = [rule.role for rule in policy.repositories]
    if len(set(roles)) != len(roles):
        raise ValueError("Azure DevOps repository roles must be unique")

    build = client.build(build_id)
    if build.get("id") != build_id:
        raise ValueError("Azure DevOps returned a different build")
    if build.get("status") != "completed" or build.get("result") != "failed":
        raise ValueError("Azure DevOps build must be completed and failed")
    timeline = client.timeline(build_id)
    records = timeline.get("records")
    if not isinstance(records, list):
        raise ValueError("Azure DevOps timeline records are unavailable")
    failed_step = _single_step(records, policy.failed_step_name, failed=True)
    provenance_step = _single_step(records, policy.provenance_step_name, failed=False)
    failed_log_id = failed_step["log"]["id"]
    provenance_log_id = provenance_step["log"]["id"]
    selected_log = select_failure_log(
        client.log(build_id, failed_log_id),
        line_limit=policy.failed_log_tail_lines,
        char_limit=policy.failed_log_max_chars,
    )
    sanitized_log = sanitize_provider_text(selected_log.text)
    provenance_log = client.log(build_id, provenance_log_id)

    definition = build.get("definition") if isinstance(build.get("definition"), dict) else {}
    build_summary = {
        "build_id": build_id,
        "build_number": build.get("buildNumber"),
        "pipeline": definition.get("name"),
        "status": build.get("status"),
        "result": build.get("result"),
        "source_branch": build.get("sourceBranch"),
        "failed_step": policy.failed_step_name,
    }
    incident = Incident(
        case_id=f"azure-devops-build-{build_id}",
        workload=policy.workload,
        question=policy.question,
        evidence=[
            Evidence(
                id="build_summary",
                source=f"Azure DevOps build {build_id}",
                text=json.dumps(build_summary, sort_keys=True),
            ),
            Evidence(
                id="failed_step_log",
                source=f"Azure DevOps build {build_id} log {failed_log_id}: {policy.failed_step_name}",
                text=sanitized_log.text,
            ),
        ],
    )

    repositories = []
    context = []
    hashes = {}
    total_chars = 0
    total_items = 0
    redactions = dict(sanitized_log.redactions)
    normalized_provenance = []
    resolved = []
    explicit: dict[str, list[str]] = {}
    for rule in policy.repositories:
        commit = _repository_commit(rule, build, provenance_log)
        normalized_provenance.append(f"{rule.role}={rule.repository}@{commit}")
        resolved.append((rule, commit))
        paths = sorted(set(rule.allowed_files))
        if len(paths) != len(rule.allowed_files):
            raise ValueError("Allowed repository files must be unique")
        explicit[rule.role] = paths
        for path in paths:
            _valid_path(path)
            content = client.repository_file(rule.repository, commit, path)
            if contains_kubernetes_secret_manifest(content):
                raise ValueError("Kubernetes Secret manifests are not allowed in provider context")
            sanitized_content = sanitize_provider_text(content)
            for kind, count in sanitized_content.redactions.items():
                redactions[kind] = redactions.get(kind, 0) + count
            excerpt, line_count = _numbered_excerpt(sanitized_content.text)
            source = excerpt_source(
                policy.project, rule, commit, path, 1, line_count, "policy_allowed_file"
            )
            context.append(Evidence(id=evidence_id(source), source=source, text=excerpt))
            hashes[f"{rule.role}:{path}"] = hashlib.sha256(content.encode()).hexdigest()
            total_chars += len(excerpt)
            total_items += 1
            if total_chars > policy.context_max_chars or total_items > policy.context_max_items:
                raise ValueError(
                    "Allowlisted repository context exceeds the configured pack bounds"
                )

    selection = None
    if policy.discovery is not None:
        found = discover_context(
            client=client,
            project=policy.project,
            policy=policy.discovery,
            rules=resolved,
            entry_role=next(
                r.role for r in policy.repositories if r.commit_source == "build_source"
            ),
            log_text=sanitized_log.text,
            taken={(role, path) for role, paths in explicit.items() for path in paths},
            total_chars=total_chars,
            total_items=total_items,
            max_total_chars=policy.context_max_chars,
            max_total_items=policy.context_max_items,
            known_text="\n".join(item.text for item in context),
        )
        context.extend(found.evidence)
        hashes.update(found.hashes)
        selection = found.summary
        for kind, count in found.redactions.items():
            redactions[kind] = redactions.get(kind, 0) + count
        for role, paths in found.files.items():
            explicit[role] = sorted([*explicit[role], *paths])
    for rule, commit in resolved:
        repositories.append(
            CapturedRepository(
                role=rule.role, repository=rule.repository, commit=commit, files=explicit[rule.role]
            )
        )
    if not context:
        raise ValueError("No repository context was selected")

    incident.evidence.append(
        Evidence(
            id="build_provenance",
            source=f"Azure DevOps build {build_id} provenance log {provenance_log_id}",
            text="; ".join(normalized_provenance),
        )
    )
    return CapturedContextPack(
        schema_version="1",
        source_type="azure_devops_build",
        source_reference=f"{policy.organization_url}/{policy.project}/_build/results?buildId={build_id}",
        incident=incident,
        repositories=repositories,
        context_evidence=context,
        content_hashes=hashes,
        safety=CaptureSafetySummary(
            raw_log_chars=selected_log.raw_chars,
            raw_log_lines=selected_log.raw_lines,
            selected_log_chars=len(sanitized_log.text),
            selected_log_lines=selected_log.selected_lines,
            selection_reasons=selected_log.reasons,
            redactions=dict(sorted(redactions.items())),
        ),
        context_selection=selection,
    )

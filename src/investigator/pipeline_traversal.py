"""Follow Azure Pipelines template references across policy-approved repositories."""

import posixpath
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

_REPOSITORY = re.compile(
    r"^[ \t]*-[ \t]*repository:[ \t]*(\S+)[ \t]*\n(.*?)(?=^[ \t]*-[ \t]*repository:|^\S|\Z)",
    re.MULTILINE | re.DOTALL,
)
_NAME = re.compile(r"^[ \t]*name:[ \t]*['\"]?([^\s'\"#]+)", re.MULTILINE)
_TEMPLATE = re.compile(r"^[ \t]*(?:-[ \t]*)?template:[ \t]*['\"]?([^\s'\"#]+)", re.MULTILINE)


class Omitted(Exception):
    """A file was not read; kind is 'policy' or 'budget'. Never carries file content."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class PipelineFile:
    role: str
    path: str
    depth: int
    text: str
    reason: str


@dataclass
class Traversal:
    files: list[PipelineFile] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    omitted_policy: int = 0
    omitted_budget: int = 0
    cycles_skipped: int = 0


def repository_aliases(text: str, repositories: dict[str, str]) -> dict[str, str]:
    """Map pipeline resource aliases to policy roles; repositories maps role -> name."""

    aliases = {}
    for match in _REPOSITORY.finditer(text):
        name = _NAME.search(match.group(2))
        if not name:
            continue
        short = name.group(1).rsplit("/", 1)[-1]
        roles = sorted(
            role for role, repo in repositories.items() if repo in (name.group(1), short)
        )
        if roles:
            aliases[match.group(1)] = roles[0]
    return aliases


def template_references(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in _TEMPLATE.finditer(text):
        value = match.group(1)
        if "${{" not in value and "$(" not in value:
            seen.setdefault(value)
    return list(seen)


def traverse(
    entry_role: str,
    entry_path: str,
    repositories: dict[str, str],
    tree: dict[str, set[str]],
    read: Callable[[str, str], str],
    max_depth: int,
    max_files: int,
) -> Traversal:
    """Deterministic breadth-first walk. read(role, path) raises Omitted when not readable."""

    result = Traversal()
    if entry_path not in tree.get(entry_role, ()):
        result.unresolved.append(f"missing_file:{entry_role}:{entry_path}")
        return result
    aliases: dict[str, str] = {}
    visited: set[tuple[str, str]] = set()
    queue = deque([(entry_role, entry_path, 0, "pipeline_entry")])
    while queue:
        role, path, depth, reason = queue.popleft()
        if (role, path) in visited:
            result.cycles_skipped += 1
            continue
        if len(result.files) >= max_files:
            result.omitted_budget += 1
            continue
        visited.add((role, path))
        try:
            text = read(role, path)
        except Omitted as omitted:
            if omitted.kind == "policy":
                result.omitted_policy += 1
            else:
                result.omitted_budget += 1
            continue
        result.files.append(PipelineFile(role, path, depth, text, reason))
        if len(result.files) == 1:
            aliases = repository_aliases(text, repositories)
            aliases["self"] = entry_role
        for reference in template_references(text):
            target, _, alias = reference.partition("@")
            target_role = aliases.get(alias) if alias else role
            if target_role is None:
                result.unresolved.append(f"unapproved_repository:{alias}")
                result.omitted_policy += 1
                continue
            base = "" if target.startswith("/") else posixpath.dirname(path)
            resolved = posixpath.normpath(posixpath.join(base, target.lstrip("/")))
            if resolved.startswith("..") or resolved.startswith("/"):
                result.unresolved.append(f"invalid_template:{reference}")
                result.omitted_policy += 1
            elif resolved not in tree.get(target_role, ()):
                result.unresolved.append(f"missing_file:{target_role}:{resolved}")
            elif depth + 1 > max_depth:
                result.omitted_budget += 1
            else:
                queue.append((target_role, resolved, depth + 1, "template_reference"))
    return result

"""Bounded access to one to three independently pinned repositories."""

import hashlib
import json
import re
from dataclasses import dataclass

from .contracts import Evidence, Incident
from .repository import Repository


@dataclass(frozen=True)
class RepositoryBinding:
    role: str
    repository: Repository


class RepositorySet:
    def __init__(self, service: str, bindings: list[RepositoryBinding]):
        if not 1 <= len(bindings) <= 3:
            raise ValueError("A repository set requires one to three repositories")
        roles = [item.role for item in bindings]
        if len(set(roles)) != len(roles):
            raise ValueError("Repository roles must be unique")
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]*", role) for role in roles):
            raise ValueError("Repository roles must be lowercase identifiers")
        self.service = service
        self._repositories = {item.role: item.repository for item in bindings}

    def inventory(self) -> dict:
        return {
            "service": self.service,
            "repositories": [
                {"role": role, **repository.inventory()}
                for role, repository in sorted(self._repositories.items())
            ],
        }

    @property
    def blobs(self) -> dict[str, str]:
        return {
            f"{role}:{path}": sha
            for role, repository in sorted(self._repositories.items())
            for path, sha in sorted(repository.blobs.items())
        }

    def approved_files(self):
        for role, repository in sorted(self._repositories.items()):
            for path, text in sorted(repository.files.items()):
                yield role, path, text

    @staticmethod
    def _with_role(role: str, item: Evidence) -> Evidence:
        source = f"role={role}; {item.source}"
        identity = hashlib.sha256(source.encode()).hexdigest()[:24]
        return Evidence(id=f"repo_{identity}", source=source, text=item.text)

    def dispatch(self, name: str, raw_arguments: str) -> list[Evidence]:
        if len(raw_arguments) > 2048:
            raise ValueError("Tool arguments exceed the limit")
        args = json.loads(raw_arguments)
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object")
        if name == "search_repository" and set(args) == {"repository", "query"}:
            role, query = args["repository"], args["query"]
            if type(role) is not str or type(query) is not str:
                raise ValueError("Invalid search arguments")
            roles = sorted(self._repositories) if role == "*" else [role]
            found = []
            for selected in roles:
                repository = self._repositories.get(selected)
                if repository is None:
                    raise ValueError("Unknown repository role")
                found.extend(self._with_role(selected, item) for item in repository.search(query))
                if len(found) >= 8:
                    return found[:8]
            return found
        required = {"repository", "path", "start", "end"}
        if name == "read_repository_file" and set(args) == required:
            role = args["repository"]
            if type(role) is not str or role not in self._repositories:
                raise ValueError("Unknown repository role")
            if type(args["path"]) is str and all(type(args[key]) is int for key in ("start", "end")):
                item = self._repositories[role].read(args["path"], args["start"], args["end"])
                return [self._with_role(role, item)]
        raise ValueError("Unknown tool or invalid arguments")

    def context_pack(
        self, incident: Incident, max_chars: int = 20000, max_items: int = 12
    ) -> list[Evidence]:
        """Select deterministic line chunks from the explicit allowlists."""
        searchable = " ".join(
            [incident.workload, incident.question, *(item.text for item in incident.evidence)]
        ).casefold()
        terms = {
            token
            for token in re.findall(r"[a-z][a-z0-9_.-]{2,}", searchable)
            if token not in {"container", "deployment", "evidence", "investigation", "synthetic"}
        }
        candidates = []
        for role, path, content in self.approved_files():
            lines = content.splitlines()
            for offset in range(0, len(lines), 80):
                numbered = []
                for number, line in enumerate(lines[offset : offset + 80], offset + 1):
                    candidate = f"{number}: {line}"
                    if numbered and len("\n".join([*numbered, candidate])) > 8000:
                        break
                    numbered.append(candidate[:8000])
                if not numbered:
                    continue
                text = "\n".join(numbered)
                haystack = f"{role} {path} {text}".casefold()
                score = sum(min(haystack.count(term), 5) for term in terms)
                source = (
                    f"role={role}; {self._repositories[role].policy.repository_id}@"
                    f"{self._repositories[role].commit}:{path}:L{offset + 1}-L{offset + len(numbered)}"
                )
                identity = hashlib.sha256(source.encode()).hexdigest()[:24]
                candidates.append(
                    (score, role, path, offset, Evidence(id=f"repo_{identity}", source=source, text=text))
                )
        selected = []
        consumed = 0
        for _, _, _, _, item in sorted(
            candidates, key=lambda value: (-value[0], value[1], value[2], value[3])
        ):
            if len(selected) >= max_items:
                break
            if consumed + len(item.text) > max_chars:
                continue
            selected.append(item)
            consumed += len(item.text)
        if not selected:
            raise ValueError("Repository context pack is empty under the configured bounds")
        return selected

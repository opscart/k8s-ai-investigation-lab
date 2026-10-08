"""Read committed Git blobs only. No checkout, hooks, remote access or file execution."""

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

from .contracts import Evidence, RepositoryPolicy


class Repository:
    def __init__(self, root: Path, commit: str, policy: RepositoryPolicy):
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            raise ValueError("Use a full immutable commit SHA, not a branch or tag")
        self.root = root.resolve(strict=True)
        self.commit = commit
        self.policy = policy
        self.files: dict[str, str] = {}
        self.blobs: dict[str, str] = {}
        if self._git("cat-file", "-t", commit).strip() != b"commit":
            raise ValueError("Repository revision is not a commit")
        entries = self._git("ls-tree", "-r", "-z", commit)
        allowed = set(policy.allowed_files)
        for path in allowed:
            parts = PurePosixPath(path).parts
            if not parts or path.startswith("/") or ".." in parts or "\\" in path:
                raise ValueError("Invalid allowed repository path")
        for entry in entries.split(b"\0"):
            if not entry:
                continue
            meta, raw_path = entry.split(b"\t", 1)
            path = raw_path.decode("utf-8")
            if path not in allowed:
                continue
            mode, kind, sha = meta.decode().split()
            if mode not in ("100644", "100755") or kind != "blob":
                raise ValueError("Allowlist contains a symlink or non-regular file")
            if int(self._git("cat-file", "-s", sha)) > 65536:
                raise ValueError("An allowed file exceeds the 64 KiB limit")
            content = self._git("cat-file", "blob", sha).decode("utf-8")
            if "\x00" in content:
                raise ValueError("An allowed file is binary")
            self.files[path] = content
            self.blobs[path] = sha
        if set(self.files) != allowed:
            raise ValueError("Some allowed files do not exist at the pinned commit")

    def _git(self, *args: str) -> bytes:
        try:
            return subprocess.run(
                ["git", "--no-replace-objects", "-C", str(self.root), *args],
                capture_output=True,
                check=True,
                timeout=10,
            ).stdout
        except (subprocess.SubprocessError, OSError) as exc:
            raise ValueError("Unable to read the pinned Git repository") from exc

    def inventory(self) -> dict:
        return {
            "repository": self.policy.repository_id,
            "commit": self.commit,
            "files": sorted(self.files),
        }

    def read(self, path: str, start: int, end: int) -> Evidence:
        if path not in self.files:
            raise ValueError("Path is not allowed")
        lines = self.files[path].splitlines()
        if start < 1 or end < start or end - start >= 120 or start > len(lines):
            raise ValueError("Request 1–120 existing lines")
        end = min(end, len(lines))
        text = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
        if len(text) > 10000:
            raise ValueError("Selected lines exceed the output limit; narrow the range")
        source = f"{self.policy.repository_id}@{self.commit}:{path}:L{start}-L{end}"
        identity = hashlib.sha256(source.encode()).hexdigest()[:24]
        return Evidence(id=f"repo_{identity}", source=source, text=text)

    def search(self, query: str) -> list[Evidence]:
        if not query.strip() or len(query) > 120:
            raise ValueError("Search query must contain 1–120 characters")
        found = []
        for path in sorted(self.files):
            for n, line in enumerate(self.files[path].splitlines(), 1):
                if query.casefold() in line.casefold():
                    found.append(self.read(path, n, n))
                    if len(found) == 8:
                        return found
        return found

    def dispatch(self, name: str, raw_arguments: str) -> list[Evidence]:
        if len(raw_arguments) > 2048:
            raise ValueError("Tool arguments exceed the limit")
        args = json.loads(raw_arguments)
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object")
        if name == "search_repository" and set(args) == {"query"}:
            if type(args["query"]) is str:
                return self.search(args["query"])
        if name == "read_repository_file" and set(args) == {"path", "start", "end"}:
            if type(args["path"]) is str and all(type(args[x]) is int for x in ("start", "end")):
                return [self.read(args["path"], args["start"], args["end"])]
        raise ValueError("Unknown tool or invalid arguments")

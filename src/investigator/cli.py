"""Preview exact inputs locally; explicit fingerprint approval gates cloud use."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from .contracts import Evidence, Incident, RepositoryPolicy
from .report import markdown
from .repository import Repository
from .runner import run


def read_bounded(path: Path, limit: int = 65536) -> str:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Input file exceeds the size limit")
    return raw.decode("utf-8")


def prepare(args):
    incident = Incident.model_validate_json(read_bounded(args.case))
    if args.logs:
        text = read_bounded(args.logs, 12000)
        incident.evidence.append(
            Evidence(id="reviewed_logs", source="operator-reviewed excerpt", text=text)
        )
    if len({item.id for item in incident.evidence}) != len(incident.evidence):
        raise ValueError("Evidence IDs must be unique")
    repository = None
    if args.mode == "agent":
        if not all((args.repo, args.commit, args.policy)):
            raise ValueError("Agent mode requires --repo, --commit and --policy")
        policy = RepositoryPolicy.model_validate_json(read_bounded(args.policy))
        repository = Repository(args.repo, args.commit, policy)
    scope = {
        "incident": incident.model_dump(),
        "mode": args.mode,
        "endpoint": os.getenv("AZURE_AI_PROJECT_ENDPOINT", ""),
        "model": os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", ""),
        "repository": repository.inventory() if repository else None,
        "blobs": repository.blobs if repository else {},
    }
    digest = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    return incident, repository, scope, digest


def parser():
    root = argparse.ArgumentParser(description="Standalone Foundry investigation lab")
    root.add_argument("action", choices=["preview", "run"])
    root.add_argument("--mode", choices=["baseline", "agent"], required=True)
    root.add_argument("--case", type=Path, required=True)
    root.add_argument("--repo", type=Path)
    root.add_argument("--commit")
    root.add_argument("--policy", type=Path)
    root.add_argument("--logs", type=Path, help="Optional edited log excerpt, at most 12,000 bytes")
    root.add_argument("--approve", help="Exact SHA-256 approval shown by preview")
    root.add_argument("--output", type=Path, default=Path(".local/results"))
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        incident, repository, scope, digest = prepare(args)
        if args.action == "preview":
            print(json.dumps(scope, indent=2))
            if repository:
                print("\nAPPROVED REPOSITORY CONTENT AVAILABLE TO THE AGENT:")
                for path, text in sorted(repository.files.items()):
                    print(f"\n--- {path} ---\n{text}")
            print("\nReview/edit inputs before sending. No cloud request was made.")
            print(f"Approval fingerprint: {digest}")
            return 0
        if args.approve != digest:
            raise ValueError(
                "Run preview and approve its exact fingerprint; changed inputs need new approval"
            )
        stem = f"{args.mode}-{digest[:12]}"
        if any((args.output / f"{stem}.{suffix}").exists() for suffix in ("json", "md")):
            raise ValueError(
                "Result already exists; choose a fresh --output directory before rerunning"
            )
        args.output.mkdir(parents=True, exist_ok=True)
        # Import only after local validation and approval; preview works offline.
        from .foundry import Foundry

        provider = Foundry(args.mode == "agent")
        try:
            with provider:
                result = run(provider, incident, repository)
                result["model"] = provider.model
        except Exception:
            raise ValueError(
                "Foundry investigation failed. Check project access, deployment support and service "
                "status. No automatic retry was made; provider error bodies are not printed."
            ) from None
        finally:
            for warning in provider.cleanup_warnings:
                print(warning, file=sys.stderr)
        result["approval_fingerprint"] = digest
        result["incident_fingerprint"] = hashlib.sha256(
            incident.model_dump_json().encode()
        ).hexdigest()
        result["cleanup_warnings"] = provider.cleanup_warnings
        # Exclusive creates avoid overwriting an earlier experimental result.
        for suffix, text in (("json", json.dumps(result, indent=2)), ("md", markdown(result))):
            path = args.output / f"{stem}.{suffix}"
            with path.open("x", encoding="utf-8") as stream:
                stream.write(text)
            print(f"Saved {path}")
        return 0
    except (ValidationError, UnicodeDecodeError, OSError):
        # Do not echo validation details: they can contain submitted log or source text.
        print(
            "Request failed. Check arguments, input schema, immutable commit, file allowlist, "
            "approval fingerprint, and output directory. See docs/troubleshooting.md.",
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

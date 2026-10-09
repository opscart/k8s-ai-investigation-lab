"""Preview exact inputs locally; explicit fingerprint approval gates cloud use."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from .contracts import (
    Evidence,
    Incident,
    RepositoryPolicy,
    RepositorySetConfig,
)
from .diagnostics import failure_details
from .report import markdown
from .repository import Repository
from .repository_set import RepositoryBinding, RepositorySet
from .runner import run


def read_bounded(path: Path, limit: int = 65536) -> str:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Input file exceeds the size limit")
    return raw.decode("utf-8")


def load_repository_set(path: Path) -> tuple[RepositorySet, RepositorySetConfig]:
    config = RepositorySetConfig.model_validate_json(read_bounded(path))
    base = path.resolve().parent
    bindings = []
    for source in config.repositories:
        root = Path(source.root)
        policy_path = Path(source.policy)
        if not root.is_absolute():
            root = base / root
        if not policy_path.is_absolute():
            policy_path = base / policy_path
        policy = RepositoryPolicy.model_validate_json(read_bounded(policy_path))
        bindings.append(RepositoryBinding(source.role, Repository(root, source.commit, policy)))
    return RepositorySet(config.service, bindings), config


def prepare(args):
    if (args.input_price_per_million is None) != (args.output_price_per_million is None):
        raise ValueError("Provide both input and output token prices, or neither")
    if any(
        value is not None and value < 0
        for value in (args.input_price_per_million, args.output_price_per_million)
    ):
        raise ValueError("Token prices cannot be negative")
    incident = Incident.model_validate_json(read_bounded(args.case))
    if args.logs:
        text = read_bounded(args.logs, 12000)
        incident.evidence.append(
            Evidence(id="reviewed_logs", source="operator-reviewed excerpt", text=text)
        )
    if len({item.id for item in incident.evidence}) != len(incident.evidence):
        raise ValueError("Evidence IDs must be unique")
    repository = None
    repository_config = None
    legacy_repository = any((args.repo, args.commit, args.policy))
    if args.repository_set and legacy_repository:
        raise ValueError("Use either --repository-set or the legacy single-repository flags")
    if args.repository_set:
        repository, repository_config = load_repository_set(args.repository_set)
    elif args.mode == "agent":
        if not all((args.repo, args.commit, args.policy)):
            raise ValueError("Agent mode requires --repository-set or --repo, --commit and --policy")
        policy = RepositoryPolicy.model_validate_json(read_bounded(args.policy))
        repository = RepositorySet(
            incident.workload,
            [RepositoryBinding("primary", Repository(args.repo, args.commit, policy))],
        )
    elif legacy_repository:
        raise ValueError("Single-repository flags are supported only in agent mode")
    if args.mode == "context-pack" and repository is None:
        raise ValueError("Context-pack mode requires --repository-set")
    if args.mode == "baseline" and repository is not None:
        raise ValueError("Baseline mode cannot use repositories")
    context_evidence = []
    if args.mode == "context-pack":
        context_evidence = repository.context_pack(
            incident,
            repository_config.context_max_chars,
            repository_config.context_max_items,
        )
    scope = {
        "incident": incident.model_dump(),
        "mode": args.mode,
        "endpoint": os.getenv("AZURE_AI_PROJECT_ENDPOINT", ""),
        "model": os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", ""),
        "repository": repository.inventory() if repository else None,
        "blobs": repository.blobs if repository else {},
        "context_pack": [item.model_dump() for item in context_evidence],
        "pricing": {
            "input_per_million": args.input_price_per_million,
            "output_per_million": args.output_price_per_million,
        },
    }
    digest = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    return incident, repository, context_evidence, scope, digest


def add_cost_estimate(stats: dict, input_price: float | None, output_price: float | None) -> None:
    if (input_price is None) != (output_price is None):
        raise ValueError("Provide both input and output token prices, or neither")
    if input_price is None:
        return
    if input_price < 0 or output_price < 0:
        raise ValueError("Token prices cannot be negative")
    stats["input_price_per_million_usd"] = input_price
    stats["output_price_per_million_usd"] = output_price
    stats["estimated_cost_usd"] = round(
        stats["input_tokens"] * input_price / 1_000_000
        + stats["output_tokens"] * output_price / 1_000_000,
        6,
    )


def parser():
    root = argparse.ArgumentParser(description="Standalone Foundry investigation lab")
    root.add_argument("action", choices=["preview", "run"])
    root.add_argument("--mode", choices=["baseline", "context-pack", "agent"], required=True)
    root.add_argument("--case", type=Path, required=True)
    root.add_argument("--repo", type=Path)
    root.add_argument("--commit")
    root.add_argument("--policy", type=Path)
    root.add_argument(
        "--repository-set",
        type=Path,
        help="JSON registry for one to three pinned repositories and their allowlists",
    )
    root.add_argument("--logs", type=Path, help="Optional edited log excerpt, at most 12,000 bytes")
    root.add_argument("--approve", help="Exact SHA-256 approval shown by preview")
    root.add_argument("--output", type=Path, default=Path(".local/results"))
    root.add_argument("--input-price-per-million", type=float)
    root.add_argument("--output-price-per-million", type=float)
    root.add_argument(
        "--debug",
        action="store_true",
        help="Print provider error details; these may echo submitted content. Use with synthetic data.",
    )
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        incident, repository, context_evidence, scope, digest = prepare(args)
        if args.action == "preview":
            print(json.dumps(scope, indent=2))
            if repository and args.mode == "agent":
                print("\nAPPROVED REPOSITORY CONTENT AVAILABLE TO THE AGENT:")
                for role, path, text in repository.approved_files():
                    print(f"\n--- {role}:{path} ---\n{text}")
            elif context_evidence:
                print("\nEXACT BOUNDED REPOSITORY CONTEXT TO BE SENT:")
                for item in context_evidence:
                    print(f"\n--- {item.id} | {item.source} ---\n{item.text}")
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
                result = run(
                    provider,
                    incident,
                    repository,
                    mode=args.mode,
                    prefetched_evidence=context_evidence,
                )
                result["model"] = provider.model
        except Exception as exc:
            print(
                json.dumps(failure_details(exc, provider.stage, args.debug), indent=2),
                file=sys.stderr,
            )
            raise ValueError(
                "Foundry investigation failed. Check project access, deployment support and service "
                "status. No automatic retry was made. Use --debug with synthetic data for provider details."
            ) from None
        finally:
            for warning in provider.cleanup_warnings:
                print(warning, file=sys.stderr)
        result["approval_fingerprint"] = digest
        result["incident_fingerprint"] = hashlib.sha256(
            incident.model_dump_json().encode()
        ).hexdigest()
        result["cleanup_warnings"] = provider.cleanup_warnings
        add_cost_estimate(
            result["stats"], args.input_price_per_million, args.output_price_per_million
        )
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

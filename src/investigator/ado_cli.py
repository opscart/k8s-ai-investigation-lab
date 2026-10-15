"""Capture Azure DevOps evidence without invoking a model."""

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from .azure_devops import AzureDevOpsClient, capture_build
from .cli import read_bounded
from .contracts import AzureDevOpsCapturePolicy


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Capture a bounded Azure DevOps build evidence pack")
    root.add_argument("capture", choices=["capture"])
    root.add_argument("--build-id", type=int, required=True)
    root.add_argument("--policy", type=Path, required=True)
    root.add_argument("--output", type=Path, required=True)
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("Capture output already exists; choose a new path")
        policy = AzureDevOpsCapturePolicy.model_validate_json(read_bounded(args.policy))
        client = AzureDevOpsClient(policy.organization_url, policy.project)
        capture = capture_build(client, policy, args.build_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(capture.model_dump_json(indent=2))
        print(f"Saved {args.output}")
        print("No model request was made. Preview and approve this exact capture before analysis.")
        return 0
    except (ValidationError, UnicodeDecodeError, OSError, ValueError):
        print(
            "Azure DevOps capture failed. Check read access, build and step names, provenance labels, "
            "repository allowlists, response bounds, and output path.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

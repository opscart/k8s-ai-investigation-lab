"""Workflow glue for the example pipeline: fingerprint extraction, manifest, pre-cloud check.

Standard library only. It never parses or prints evidence; errors use fixed messages.
Capture, approval fingerprints and evaluation stay in the investigate* commands.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA = "investigation-review/v1"
REVIEW_FILES = ("capture.json", "preview.txt", "fingerprint.txt")
_FINGERPRINT_LINE = re.compile(r"Approval fingerprint: ([0-9a-f]{64})")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")


class GuardError(Exception):
    """Carries a fixed message only; never evidence text."""


def extract_fingerprint(preview: str) -> str:
    lines = preview.splitlines()
    found = [m.group(1) for line in lines if (m := _FINGERPRINT_LINE.fullmatch(line))]
    last = next((line for line in reversed(lines) if line.strip()), "")
    if len(found) != 1 or not _FINGERPRINT_LINE.fullmatch(last):
        raise GuardError("Preview must end with exactly one approval fingerprint line")
    return found[0]


def _sha256(path: Path) -> dict:
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def build_manifest(directory: Path, failed_build_id: int, run_id: int) -> dict:
    missing = [name for name in REVIEW_FILES if not (directory / name).is_file()]
    if missing:
        raise GuardError("Review directory is missing required files")
    return {
        "schema": SCHEMA,
        "failed_build_id": failed_build_id,
        "pipeline_run_id": run_id,
        "files": {name: _sha256(directory / name) for name in REVIEW_FILES},
    }


def verify(directory: Path, failed_build_id: int, run_id: int, preview: str) -> str:
    """Return the approved fingerprint only if every binding holds; otherwise raise."""

    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise GuardError("Review manifest is missing or unreadable") from None
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise GuardError("Review manifest has an unexpected schema")
    if manifest.get("pipeline_run_id") != run_id:
        raise GuardError("Review artifact was not created by this pipeline run")
    if manifest.get("failed_build_id") != failed_build_id:
        raise GuardError("Review artifact is for a different failed build")
    present = sorted(p.name for p in directory.iterdir())
    if present != sorted([*REVIEW_FILES, "manifest.json"]):
        raise GuardError("Review artifact contains unexpected or missing files")
    expected = build_manifest(directory, failed_build_id, run_id)["files"]
    if manifest.get("files") != expected:
        raise GuardError("Review artifact files differ from the approved manifest")
    saved = (directory / "fingerprint.txt").read_text(encoding="utf-8").strip()
    if not _FINGERPRINT.fullmatch(saved):
        raise GuardError("Saved fingerprint is malformed")
    if extract_fingerprint(preview) != saved:
        raise GuardError("Recomputed approval fingerprint does not match the approved one")
    return saved


def _write_new(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    fingerprint = commands.add_parser("fingerprint")
    fingerprint.add_argument("--preview", type=Path, required=True)
    fingerprint.add_argument("--output", type=Path, required=True)
    for name in ("manifest", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--directory", type=Path, required=True)
        command.add_argument("--failed-build-id", type=int, required=True)
        command.add_argument("--run-id", type=int, required=True)
    commands.choices["manifest"].add_argument("--output", type=Path, required=True)
    commands.choices["verify"].add_argument("--preview", type=Path, required=True)
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "fingerprint":
            preview = args.preview.read_text(encoding="utf-8")
            _write_new(args.output, extract_fingerprint(preview) + "\n")
        elif args.command == "manifest":
            manifest = build_manifest(args.directory, args.failed_build_id, args.run_id)
            _write_new(args.output, json.dumps(manifest, indent=2) + "\n")
        else:
            preview = args.preview.read_text(encoding="utf-8")
            verify(args.directory, args.failed_build_id, args.run_id, preview)
            print("Recomputed fingerprint matches the approved review artifact.")
        return 0
    except GuardError as exc:
        print(str(exc), file=sys.stderr)
    except (OSError, UnicodeDecodeError):
        print("Workflow guard could not read or write a required file.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

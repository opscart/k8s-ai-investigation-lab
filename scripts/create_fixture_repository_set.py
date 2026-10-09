"""Create an ignored, runnable repository-set registry for the synthetic fixture."""

import argparse
import json
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--output", type=Path, default=Path(".local/multi-repo-set.json"))
    args = parser.parse_args()
    root = Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", f"{args.revision}^{{commit}}"], text=True
    ).strip()
    roles = ("application", "deployment", "platform")
    payload = {
        "service": "investigation-lab/checkout-demo",
        "repositories": [
            {
                "role": role,
                "root": str(root),
                "commit": commit,
                "policy": str(root / "config" / f"multi-repo-{role}-policy.json"),
            }
            for role in roles
        ],
        "context_max_chars": 12000,
        "context_max_items": 9,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()

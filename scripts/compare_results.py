"""Compare saved runs without another AI call. Human scoring stays separate."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path, nargs="+")
    args = parser.parse_args()
    if len(args.results) < 2:
        raise SystemExit("Provide at least two saved results")
    results = [json.loads(path.read_text()) for path in args.results]
    identities = {(item["incident_fingerprint"], item["model"]) for item in results}
    if len(identities) != 1:
        raise SystemExit("Comparison requires identical incident inputs and model deployment")
    if len({item["mode"] for item in results}) != len(results):
        raise SystemExit("Provide at most one result per mode")
    for result in results:
        print(result["mode"], json.dumps(result["stats"]))
        print("Cause:", result["diagnosis"]["likely_cause"]["text"])
        print("Correction:", result["diagnosis"]["proposed_correction"])
    print("Score results against evals/expected/rubric.md. No automatic correctness score is claimed.")


if __name__ == "__main__":
    main()

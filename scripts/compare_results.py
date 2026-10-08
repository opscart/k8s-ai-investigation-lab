"""Compare saved runs without another AI call. Human scoring stays separate."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("agent", type=Path)
    args = parser.parse_args()
    base, agent = (json.loads(path.read_text()) for path in (args.baseline, args.agent))
    if base["mode"] != "baseline" or agent["mode"] != "agent":
        raise SystemExit("Expected baseline then agent result")
    if (base["incident_fingerprint"], base["model"]) != (
        agent["incident_fingerprint"],
        agent["model"],
    ):
        raise SystemExit("Comparison requires identical incident inputs and model deployment")
    for result in (base, agent):
        print(result["mode"], json.dumps(result["stats"]))
        print("Cause:", result["diagnosis"]["likely_cause"]["text"])
        print("Correction:", result["diagnosis"]["proposed_correction"])
    print("Score both against evals/expected/rubric.md. No automatic correctness score is claimed.")


if __name__ == "__main__":
    main()

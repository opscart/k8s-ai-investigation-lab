"""Evaluate saved results offline: no network, no model, no Azure access."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .cli import read_bounded
from .eval_report import markdown
from .evaluation import (
    EvalCase,
    ReviewBindingError,
    ReviewRecord,
    digest,
    evaluate,
    render_json,
)

EXIT_CODES = {"pass": 0, "fail": 1, "human_review_required": 3}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Deterministic evaluation of saved investigations")
    root.add_argument("action", choices=["evaluate"])
    root.add_argument("--case", type=Path, required=True)
    root.add_argument("--capture", type=Path, required=True)
    root.add_argument("--result", type=Path, required=True)
    root.add_argument("--review", type=Path, help="Optional recorded human review JSON")
    root.add_argument("--output", type=Path, required=True)
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        raw = {
            "case": read_bounded(args.case, 262144),
            "capture": read_bounded(args.capture, 1048576),
            "result": read_bounded(args.result, 1048576),
        }
        review_raw = read_bounded(args.review, 65536) if args.review else None
        case = EvalCase.model_validate_json(raw["case"])
        capture, result = json.loads(raw["capture"]), json.loads(raw["result"])
        if not isinstance(capture, dict) or not isinstance(result, dict):
            raise ValueError("Capture and result must be JSON objects")
        review = ReviewRecord.model_validate_json(review_raw) if review_raw else None
        digests = {name: digest(text.encode()) for name, text in raw.items()}
        report = evaluate(case, capture, result, review, digests)
        for suffix, text in (("json", render_json(report)), ("md", markdown(report))):
            args.output.mkdir(parents=True, exist_ok=True)
            with (args.output / f"evaluation.{suffix}").open("x", encoding="utf-8") as stream:
                stream.write(text)
        print(f"{report['overall_status']}: {args.output}/evaluation.json")
        if report["overall_status"] == "human_review_required":
            print("Semantic correctness is pending human review; record answers with --review.")
        return EXIT_CODES[report["overall_status"]]
    except ReviewBindingError as exc:
        print(f"{exc}. Re-record the review for these exact files.", file=sys.stderr)
        return 2
    except (
        ValidationError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        # Inputs may contain submitted text; report the error class only.
        print(
            f"Evaluation input error ({type(exc).__name__}). Check files and schemas; "
            "existing outputs are never overwritten.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

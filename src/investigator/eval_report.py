"""Markdown rendering of an evaluation report."""


def _text(value) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_text(item)}" for key, item in value.items())
    if isinstance(value, list):
        return ", ".join(_text(item) for item in value) or "none"
    return str(value)


def _cell(value) -> str:
    return _text(value).replace("|", "\\|").replace("\n", " ")


def _status(item: dict) -> str:
    if item["matched"] is None:
        return "not_applicable"
    return "matched" if item["matched"] else "NOT matched"


def markdown(report: dict) -> str:
    lines = [
        f"# Evaluation: {report['case_id']}",
        f"**Overall status: {report['overall_status']}**",
        report["disclaimer"],
        "## Hard gates",
        "| Gate | Category | Result | Observed | Expected |",
        "|---|---|---|---|---|",
    ]
    for gate in report["hard_gates"]:
        verdict = "PASS" if gate["passed"] else "FAIL"
        lines.append(
            f"| {gate['id']} | {gate['category']} | {verdict} | "
            f"{_cell(gate['observed'])} | {_cell(gate['expected'])} |"
        )
    stats = report["statistics"]
    lines += [
        "## Statistics",
        f"Mode: {stats['mode']} | Model: {stats['model']} | Excerpts: {stats['excerpts']} | "
        f"Gates: {stats['gates_passed']}/{stats['gates_total']}",
        f"Run stats: {stats['stats']}",
        "## Textual heuristics (not gates)",
    ]
    lines += [
        f"- {_status(item)}: {item['id']}"
        + (f" ({_text(item['detail'])})" if "detail" in item else "")
        for item in report["textual_heuristics"]
    ]
    review = report["human_review"]
    lines.append(f"## Human review (reviewer: {review['reviewer'] or 'none recorded'})")
    lines += [f"- [{q['answer']}] {q['question']}" for q in review["questions"]]
    lines.append("## Input digests")
    lines += [f"- {name}: {value}" for name, value in report["input_digests"].items()]
    return "\n".join(lines) + "\n"

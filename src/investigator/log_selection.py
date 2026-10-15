"""Select compact, explainable failure evidence from a bounded build log."""

import re
from dataclasses import dataclass

_ANCHORS = (
    ("azure_error", re.compile(r"##\[error\]", re.IGNORECASE)),
    ("error_marker", re.compile(r"\[(?:ERROR|FATAL)\]", re.IGNORECASE)),
    ("exception", re.compile(r"\b(?:[A-Za-z_$][\w$]*(?:Exception|Error)|Exception)\b")),
    ("failure_summary", re.compile(r"\b(?:build|test(?:s)?)\s+fail(?:ed|ure)\b", re.IGNORECASE)),
    ("stack_frame", re.compile(r"^\s*at\s+[\w.$]+\([^():]+:\d+\)")),
    ("source_location", re.compile(r"\b[^\s:]+\.(?:java|kt|go|py|js|ts|cs|cpp|c|h):\d+\b")),
    ("exit_code", re.compile(r"\b(?:exited?|exit code)\b.*\b[1-9]\d*\b", re.IGNORECASE)),
    (
        "compiler_error",
        re.compile(r"\b(?:fatal error|compilation error|syntax error)\b", re.IGNORECASE),
    ),
)

_NOISE = (
    re.compile(r"^\s*(?:Progress \(|Downloaded from |Downloading from )"),
    re.compile(r"^\s*\d{1,3}(?:\.\d+)?%\s*$"),
)


@dataclass(frozen=True)
class LogSelection:
    text: str
    raw_chars: int
    raw_lines: int
    selected_chars: int
    selected_lines: int
    reasons: dict[str, int]


def _reasons(line: str) -> list[str]:
    return [name for name, expression in _ANCHORS if expression.search(line)]


def _is_noise(line: str) -> bool:
    return any(expression.search(line) for expression in _NOISE)


def _bounded_render(
    lines: list[str],
    indices: list[int],
    anchors: dict[int, list[str]],
    line_limit: int,
    char_limit: int,
) -> list[int]:
    """Prefer the latest failure region while retaining the first useful anchor."""

    candidates = indices[-line_limit:]
    first_anchor = min(anchors) if anchors else None
    if first_anchor is not None and first_anchor not in candidates:
        candidates = [first_anchor, *candidates[1:]]
        candidates = sorted(set(candidates))
    while candidates:
        rendered = "\n".join(f"L{index + 1}: {lines[index]}" for index in candidates)
        if len(rendered) <= char_limit:
            return candidates
        removable = next(
            (index for index in candidates if index not in anchors and index != first_anchor), None
        )
        if removable is None:
            removable = candidates[0]
        candidates.remove(removable)
    return []


def select_failure_log(
    text: str,
    *,
    line_limit: int,
    char_limit: int,
    context_before: int = 3,
    context_after: int = 8,
) -> LogSelection:
    """Return line-numbered failure windows and selection-reason counts."""

    lines = text.splitlines()
    if not lines:
        raise ValueError("The selected Azure DevOps log is empty")
    anchors = {index: found for index, line in enumerate(lines) if (found := _reasons(line))}
    if anchors:
        selected = set()
        for index in anchors:
            selected.update(
                range(max(0, index - context_before), min(len(lines), index + context_after + 1))
            )
        indices = [
            index for index in sorted(selected) if index in anchors or not _is_noise(lines[index])
        ]
    else:
        indices = [
            index
            for index in range(max(0, len(lines) - line_limit), len(lines))
            if not _is_noise(lines[index])
        ]
    indices = _bounded_render(lines, indices, anchors, line_limit, char_limit)
    if not indices:
        raise ValueError("No bounded Azure DevOps log evidence remained after selection")
    rendered = "\n".join(f"L{index + 1}: {lines[index]}" for index in indices)
    reason_counts: dict[str, int] = {}
    for index in indices:
        for reason in anchors.get(index, []):
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    if not reason_counts:
        reason_counts["bounded_tail_fallback"] = 1
    return LogSelection(
        text=rendered,
        raw_chars=len(text),
        raw_lines=len(lines),
        selected_chars=len(rendered),
        selected_lines=len(indices),
        reasons=dict(sorted(reason_counts.items())),
    )

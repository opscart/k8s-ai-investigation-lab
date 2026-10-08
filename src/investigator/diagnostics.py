"""Bounded, opt-in provider diagnostics; no request headers or full bodies."""

import json

from pydantic import ValidationError


def failure_details(exc: Exception, stage: str, debug: bool) -> dict:
    details = {"stage": stage, "exception": type(exc).__name__}
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        details["http_status"] = status
    if isinstance(exc, ValidationError):
        details["validation_error_count"] = exc.error_count()
    if not debug:
        return details
    # OpenAI errors expose body; Azure SDK errors expose a parsed error object.
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else None
    if not isinstance(error, dict):
        parsed = getattr(exc, "error", None)
        error = {key: getattr(parsed, key, None) for key in ("code", "message")}
    selected = {
        key: value[:2000]
        for key in ("code", "param", "message")
        if isinstance((value := error.get(key)), str)
    }
    if selected:
        details["provider_error"] = selected
    elif isinstance(exc, ValueError) and not isinstance(exc, ValidationError):
        details["local_error"] = str(exc)[:2000]
    # JSON output escapes control characters; never print raw exception repr/traceback.
    json.dumps(details)
    return details

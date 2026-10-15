"""Conservative local credential redaction for provider-bound text."""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath


class SensitiveDataError(ValueError):
    """Raised when provider-bound text still contains a recognized credential."""


@dataclass(frozen=True)
class SanitizedText:
    text: str
    redactions: dict[str, int]


_PATTERNS = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?"
            r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    (
        "bearer_token",
        re.compile(r"(?i)(\bAuthorization\s*:\s*Bearer\s+)([^\s,;]+)"),
    ),
    (
        "authorization_header",
        re.compile(r"(?i)(\bAuthorization\s*:\s*(?:Basic|Token|ApiKey)\s+)([^\s,;]+)"),
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    (
        "known_token",
        re.compile(
            r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
            r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b"
        ),
    ),
    (
        "basic_auth_url",
        re.compile(r"(?i)(https?://[^\s/:@]+:)([^\s/@]+)(@)"),
    ),
    (
        "sensitive_json",
        re.compile(
            r'(?i)("(?:password|passwd|pwd|secret|client_secret|api[_-]?key|access[_-]?token|'
            r'refresh[_-]?token|accountkey|sharedaccesskey|sig)"\s*:\s*")([^"]*)(")'
        ),
    ),
    (
        "sensitive_assignment",
        re.compile(
            r"(?i)(\b(?:password|passwd|pwd|secret|client_secret|api[_-]?key|access[_-]?token|"
            r"refresh[_-]?token|accountkey|sharedaccesskey|sig)\b\s*[:=]\s*)"
            r"(?!\[REDACTED:)([^\s,;&]+)"
        ),
    ),
    (
        "sensitive_xml",
        re.compile(
            r"(?is)(<(?:password|passwd|secret|clientSecret|apiKey|accessToken|refreshToken)>)"
            r"(.*?)(</(?:password|passwd|secret|clientSecret|apiKey|accessToken|refreshToken)>)"
        ),
    ),
)

_FORBIDDEN_SUFFIXES = {".key", ".pem", ".pfx", ".p12", ".jks", ".keystore"}


def repository_path_allowed(path: str) -> bool:
    candidate = PurePosixPath(path)
    name = candidate.name.lower()
    if name == ".env" or name.startswith(".env."):
        return False
    return candidate.suffix.lower() not in _FORBIDDEN_SUFFIXES


def _replacement(kind: str, match: re.Match) -> str:
    placeholder = f"[REDACTED:{kind.upper()}]"
    if kind == "private_key":
        return placeholder + ("\n" * match.group(0).count("\n"))
    if kind in {
        "authorization_header",
        "bearer_token",
        "basic_auth_url",
        "sensitive_json",
        "sensitive_assignment",
        "sensitive_xml",
    }:
        if kind == "basic_auth_url":
            return f"{match.group(1)}{placeholder}{match.group(3)}"
        if kind in {"sensitive_json", "sensitive_xml"}:
            return f"{match.group(1)}{placeholder}{match.group(3)}"
        return f"{match.group(1)}{placeholder}"
    return placeholder


def sanitize_provider_text(text: str) -> SanitizedText:
    """Redact recognized credentials and reject any recognizable residual."""

    sanitized = text
    counts: dict[str, int] = {}
    for kind, expression in _PATTERNS:
        sanitized, count = expression.subn(
            lambda match, name=kind: _replacement(name, match), sanitized
        )
        if count:
            counts[kind] = counts.get(kind, 0) + count
    for kind, expression in _PATTERNS:
        for match in expression.finditer(sanitized):
            if "[REDACTED:" not in match.group(0):
                raise SensitiveDataError(f"Provider-bound text contains residual {kind}")
    return SanitizedText(text=sanitized, redactions=dict(sorted(counts.items())))


def contains_kubernetes_secret_manifest(text: str) -> bool:
    return bool(re.search(r"(?im)^\s*kind\s*:\s*Secret\s*(?:#.*)?$", text))

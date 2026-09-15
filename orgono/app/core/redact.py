"""Secret redaction.

This runs before anything is logged and before anything is sent off the machine.
It is deterministic Python with tests (tests/test_redaction.py), not a prompt --
if correctness depends on it, it cannot be a model's judgement.

The rules are deliberately over-eager: a redacted non-secret costs a reader a
little context, an un-redacted secret costs a credential rotation.
"""

from __future__ import annotations

import re

PLACEHOLDER = "[REDACTED]"

# Keys whose *values* are always redacted, wherever they appear as assignments.
SECRET_KEY_PATTERN = (
    r"(?:api[-_]?key|apikey|secret|token|passwd|password|pwd|auth|credential"
    r"|private[-_]?key|access[-_]?key|client[-_]?secret|session[-_]?key|bearer)"
)

# Order matters. Provider-shaped tokens are matched FIRST so that a bare token is
# fully redacted before the generic assignment rule can match only part of it.
# (Found by running it: "Authorization: Bearer <tok>" previously redacted the word
# "Bearer" and left the token in the clear.)
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pem", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----", re.S)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("openai", re.compile(r"\bsk-(?:proj-|ant-|or-v1-)?[A-Za-z0-9_\-]{16,}")),
    ("github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    ("slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("google", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("aws_akid", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("stripe", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    # user:password@host in a connection URL
    ("url_userinfo", re.compile(r"(?i)(?P<prefix>[a-z][a-z0-9+.\-]*://[^\s:/@]+:)(?P<value>[^\s@/]+)(?P<suffix>@)")),
    # Authorization: Bearer <token>  (also Token/Basic)
    ("bearer", re.compile(r"(?i)\b(?P<prefix>bearer\s+|token\s+|basic\s+)(?P<value>[A-Za-z0-9._\-+/=]{8,})")),
    # KEY=value / KEY: value / "key": "value"  (env files, YAML, JSON, source).
    # The value may carry an auth scheme word, so "auth: Bearer xyz" redacts the
    # whole thing rather than just the scheme.
    (
        "assignment",
        re.compile(
            rf'''(?ix)
            (?P<prefix>
                ["\'`]?\b\w*{SECRET_KEY_PATTERN}\w*["\'`]?
                \s*(?:=|:|=>|:=)\s*
            )
            (?P<value>
                (?:(?:bearer|token|basic)\s+)?
                (?:"[^"\n]*"|\'[^\'\n]*\'|`[^`\n]*`|[^\s,;)\]}}#]+)
            )
            ''',
        ),
    ),
)


def redact_text(text: str) -> tuple[str, int]:
    """Redact secrets in `text`. Returns (redacted_text, number_of_redactions)."""
    if not text:
        return text, 0
    count = 0

    def _sub_valued(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        prefix = match.group("prefix")
        suffix = match.groupdict().get("suffix") or ""
        value = match.group("value")
        if "[REDACT" in value:
            count -= 1
            return match.group(0)
        return f"{prefix}{PLACEHOLDER}{suffix}"

    def _sub_whole(match: re.Match[str]) -> str:
        nonlocal count
        if "[REDACT" in match.group(0):
            return match.group(0)
        count += 1
        return PLACEHOLDER

    out = text
    for name, pattern in _RULES:
        if name in {"assignment", "bearer", "url_userinfo"}:
            out = pattern.sub(_sub_valued, out)
        else:
            out = pattern.sub(_sub_whole, out)
    return out, count


def redact_obj(obj):
    """Recursively redact strings inside JSON-shaped data.

    Dict keys that look secret have their values replaced outright, so a value
    that is not itself key-shaped (``{"api_key": "hunter2"}``) is still caught.
    """
    if isinstance(obj, str):
        return redact_text(obj)[0]
    if isinstance(obj, dict):
        out = {}
        key_re = re.compile(rf"(?i){SECRET_KEY_PATTERN}")
        for k, v in obj.items():
            if isinstance(k, str) and key_re.search(k):
                out[k] = PLACEHOLDER
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj


def looks_secret(text: str) -> bool:
    """True if `text` contains something that would be redacted."""
    return redact_text(text)[1] > 0

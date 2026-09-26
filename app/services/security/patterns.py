# app/services/security/patterns.py
"""
Regex patterns for secret redaction and prompt-injection detection.

Compiled once at import. Two categories:

  REDACTION_PATTERNS — (compiled_regex, replacement) pairs applied to any
                       string before it is logged or persisted. Conservative:
                       each one targets a specific, well-known secret format.

  INJECTION_PATTERNS — (compiled_regex, weight, signal_name) triples applied
                       to retrieved content to detect prompt-injection
                       attempts. Weighted scoring, not blocking.
"""
from __future__ import annotations

import re

# ---------- redaction ----------

REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Bearer tokens: "Bearer eyJ...", "Bearer sk-...", etc.
    (re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
     "Bearer [REDACTED]"),

    # JWTs: three base64url segments separated by dots, first starting 'eyJ'.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
     "[REDACTED JWT]"),

    # key=value or key: value where the key looks secret-bearing.
    # Handles api_key, api-key, apikey, secret, password, passwd, token, auth.
    (re.compile(
        r"""(?ix)
        \b
        (api[_-]?key|apikey|secret|password|passwd|token|auth)
        \s*[:=]\s*
        ["']?
        ([^\s"',}\]]+)
        """
     ),
     r"\1=[REDACTED]"),

    # OpenAI-style keys: sk-xxxxx
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
     "[REDACTED KEY]"),

    # AWS-style access key id: AKIA + 16 uppercase alnum
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
     "[REDACTED AWS KEY]"),

    # Private key headers.
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
     "[REDACTED PRIVATE KEY]"),
]


# ---------- prompt injection ----------

# Each entry: (pattern, weight, signal_name).
# Weights are calibrated so that a single obvious signal (e.g. "ignore
# previous instructions") scores around 0.5, and two or three concurrent
# signals push above 0.8.

INJECTION_PATTERNS: list[tuple[re.Pattern[str], float, str]] = [
    # Direct override attempts.
    (re.compile(
        r"(?i)\b(ignore|disregard|forget)\s+(?:all\s+)?(?:the\s+)?"
        r"(?:previous|prior|above|earlier|preceding)\s+"
        r"(?:instructions?|prompts?|rules?|directions?)"
     ), 0.6, "ignore_previous"),

    # Role-play and impersonation.
    (re.compile(
        r"(?i)\byou\s+are\s+(?:now|no longer)\b|"
        r"\bact\s+as\s+(?:if\s+you\s+are\s+)?(?:a|an|the)\b|"
        r"\bpretend\s+(?:to\s+be|you\s+are)\b"
     ), 0.4, "role_play"),

    # System-prompt extraction.
    (re.compile(
        r"(?i)\b(reveal|show|print|output|repeat|disclose)\s+"
        r"(?:your\s+)?(?:system\s+)?"
        r"(?:prompt|instructions?|rules?|guidelines?)"
     ), 0.6, "prompt_extraction"),

    # Instruction-marking.
    (re.compile(
        r"(?i)\bnew\s+instructions?\b|"
        r"\boverride\s+(?:the\s+)?(?:previous|system)\b|"
        r"\bjailbreak\b"
     ), 0.5, "override_marker"),

    # Role tags at line start (chat-template confusion).
    (re.compile(
        r"(?m)^\s*(?:system|assistant|developer)\s*[:>]",
        re.IGNORECASE,
     ), 0.4, "role_tag"),

    # Control tokens.
    (re.compile(r"<\|(?:im_start|im_end|endoftext|system|user|assistant)\|>"),
     0.5, "control_token"),

    # "Ignore the above" style reference to context.
    (re.compile(
        r"(?i)\b(?:the\s+)?above\s+(?:is|was|should\s+be)\s+"
        r"(?:ignored|disregarded)\b"
     ), 0.5, "above_reference"),
]


def scan_injection(text: str) -> tuple[float, list[str]]:
    """
    Score a piece of text for prompt-injection signals.

    Returns (score, signals). Score is capped at 1.0. Signals is the list
    of matched signal names.
    """
    if not text:
        return 0.0, []
    score = 0.0
    signals: list[str] = []
    for pattern, weight, name in INJECTION_PATTERNS:
        if pattern.search(text):
            score += weight
            signals.append(name)
    return min(score, 1.0), signals
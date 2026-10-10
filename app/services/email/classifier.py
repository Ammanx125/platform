# app/services/email/classifier.py
"""
Email classification: intent and urgency.

This is deliberately the same shape as app/services/understanding/matcher.py:

  - An EmailClassifier Protocol defines the interface.
  - KeywordClassifier is the v1 deterministic implementation.
  - get_classifier() is the injection point; when an LLM-backed classifier
    lands, it replaces the return value here and nothing else changes.

Why deterministic first:
  The LLM provider is currently mock/gemini and is not production-grade.
  Shipping a classifier that depends on it would make the whole email
  feature unreliable. The deterministic classifier gives the demo honest
  behavior: it can be reasoned about, tested, and explained to a customer.

  When the LLM-backed classifier is ready, it will be a drop-in: same
  Protocol, better recall, same output shape. We do not design around it
  now because we do not want to commit to a prompt or a taxonomy before
  the model is trustworthy.

The classifier must be pure: given the same subject+body, it must return
the same classification. No randomness, no time dependence, no I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

# Intent categories. Kept small and business-oriented; anything we can't
# confidently place lands in "unknown" rather than being miscategorized.
INTENT_COMPLAINT = "complaint"
INTENT_INQUIRY = "inquiry"
INTENT_ORDER = "order"
INTENT_INVOICE = "invoice"
INTENT_SUPPORT = "support"
INTENT_FEEDBACK = "feedback"
INTENT_INTERNAL = "internal"
INTENT_UNKNOWN = "unknown"

ALL_INTENTS = (
    INTENT_COMPLAINT,
    INTENT_INQUIRY,
    INTENT_ORDER,
    INTENT_INVOICE,
    INTENT_SUPPORT,
    INTENT_FEEDBACK,
    INTENT_INTERNAL,
    INTENT_UNKNOWN,
)

# Urgency levels. Ordered, so comparisons like "urgent > high" work.
URGENCY_LOW = "low"
URGENCY_NORMAL = "normal"
URGENCY_HIGH = "high"
URGENCY_URGENT = "urgent"

ALL_URGENCIES = (URGENCY_LOW, URGENCY_NORMAL, URGENCY_HIGH, URGENCY_URGENT)

# Classifier version stamp. Bump on any behavior change so historic
# classifications remain interpretable.
CLASSIFIER_VERSION = "keyword-v1"


@dataclass(frozen=True)
class EmailClassification:
    """
    Output of classification.

    confidence: 0.0-1.0, an honest estimate of how sure the classifier is.
                The deterministic classifier is deliberately conservative:
                a category with no keyword match returns INTENT_UNKNOWN
                with low confidence, not a guess.

    matched:    list of keywords/patterns that fired. Useful for the audit
                trail and for debugging misclassifications. Never shown
                to the user as-is.

    method:     the classifier's identifier, so we can tell later which
                version produced a given row.

    Note: the classifier stores the canonical confidence as a 0.0-1.0 value
          for logic and downstream APIs. UI/display code may still present it
          as a percentage by multiplying by 100.
    """
    intent: str
    urgency: str
    confidence: float
    matched: list[str]
    method: str = CLASSIFIER_VERSION

    @property
    def confidence_pct(self) -> float:
        """Display-safe percentage form of confidence, preserving 0-100 scale."""
        return self.confidence * 100.0

    @property
    def confidence_percent(self) -> float:
        """Alias kept for display code that expects a percentage."""
        return self.confidence_pct

    def display_confidence(self) -> str:
        """Return confidence as a percentage string like '85%'."""
        return f"{self.confidence_pct:.0f}%"


class EmailClassifier(Protocol):
    name: str

    def classify(
        self,
        *,
        subject: str,
        body_text: str,
        from_address: str,
        to_addresses: list[str],
    ) -> EmailClassification: ...


# --- deterministic classifier ---------------------------------------------

# Intent keyword sets. Kept lowercase; the matcher lowercases input.
# Order matters: the classifier tries intents in the order listed in
# _INTENT_RULES and stops at the first match. Complaint before inquiry,
# for example, so "why hasn't this arrived, this is unacceptable"
# classifies as complaint, not inquiry.
_INTENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    (INTENT_COMPLAINT, (
        "complaint", "complain", "unacceptable", "terrible", "awful",
        "disappointed", "disappointing", "frustrated", "frustrating",
        "angry", "upset", "not happy", "unhappy", "refund", "broken",
        "faulty", "damaged", "late delivery", "delayed", "delay",
        "still waiting", "no response", "ignored", "poor service",
        "worst", "never received", "missing", "wrong item",
    )),
    (INTENT_INVOICE, (
        "invoice", "billing", "bill", "payment", "receipt",
        "purchase order", "statement", "remittance", "outstanding balance",
        "overdue", "due date", "paid", "charge", "credit note",
    )),
    (INTENT_ORDER, (
        "order", "purchase", "buy", "quote", "quotation", "rfq",
        "request for quote", "place an order", "new order", "reorder",
        "supply", "delivery of", "shipment of", "please send",
    )),
    (INTENT_SUPPORT, (
        "support", "help", "issue", "problem", "error", "not working",
        "doesn't work", "does not work", "bug", "how do i", "how can i",
        "unable to", "cannot access", "can't access", "trouble",
        "troubleshooting", "reset", "login", "password",
    )),
    (INTENT_FEEDBACK, (
        "thank you", "thanks", "great", "excellent", "appreciate",
        "well done", "happy with", "pleased", "satisfied", "good job",
        "feedback", "suggestion", "would be nice", "could you consider",
    )),
    (INTENT_INQUIRY, (
        "inquiry", "enquiry", "question", "asking", "wondering",
        "could you tell", "would like to know", "information about",
        "more details", "clarification", "confirm", "confirming",
    )),
]

# Urgency keywords. If more than one matches, the highest wins.
_URGENCY_RULES: list[tuple[str, tuple[str, ...]]] = [
    (URGENCY_URGENT, (
        "urgent", "urgently", "asap", "immediately", "right away",
        "critical", "emergency", "time-sensitive", "deadline today",
        "by end of day", "eod today", "escalate", "escalation",
    )),
    (URGENCY_HIGH, (
        "important", "priority", "high priority", "today", "tomorrow",
        "soon", "deadline", "overdue", "past due", "follow up",
        "following up", "second request", "third request", "reminder",
    )),
    (URGENCY_LOW, (
        "no rush", "whenever", "when you get a chance", "at your convenience",
        "fyi", "for your information", "just letting you know",
        "no hurry", "low priority",
    )),
    # URGENCY_NORMAL is the fallback when nothing else matches.
]

# Word-boundary-safe matcher. We do not want "order" to match "border",
# or "delay" to match "delayed" incorrectly across word boundaries.
# Multi-word phrases are matched literally.
def _match_any(haystack: str, needles: tuple[str, ...]) -> list[str]:
    matched: list[str] = []
    for n in needles:
        if " " in n:
            if n in haystack:
                matched.append(n)
        else:
            # \b around the word, case-insensitive; haystack is already lower.
            if re.search(rf"\b{re.escape(n)}\b", haystack):
                matched.append(n)
    return matched


class KeywordClassifier:
    """
    Deterministic keyword/regex classifier.

    Deliberately conservative. When no intent keyword matches, returns
    INTENT_UNKNOWN rather than guessing. When no urgency keyword matches,
    returns URGENCY_NORMAL — the business-honest default for a normal
    inbound business email.
    """
    name = CLASSIFIER_VERSION

    def classify(
        self,
        *,
        subject: str,
        body_text: str,
        from_address: str,
        to_addresses: list[str],
    ) -> EmailClassification:
        # Internal mail: from and to share the same domain, and the body
        # has no strong external cues. This is a heuristic and will misfire
        # when a customer and the tenant share a domain; the classifier
        # exposes the matched reason so a reviewer can see what happened.
        if _looks_internal(from_address, to_addresses):
            intent = INTENT_INTERNAL
            matched = ["internal_domain_match"]
            confidence = 0.6
            # Internal mail still gets an urgency pass.
            urgency, urgency_matches = _classify_urgency(subject, body_text)
            return EmailClassification(
                intent=intent,
                urgency=urgency,
                confidence=confidence,
                matched=matched + urgency_matches,
            )

        haystack = f"{subject}\n{body_text}".lower()
        intent, matched = _classify_intent(haystack)
        urgency, urgency_matches = _classify_urgency(subject, body_text)

        if intent == INTENT_UNKNOWN:
            confidence = 0.3
        elif len(matched) >= 3:
            confidence = 0.85
        elif len(matched) == 2:
            confidence = 0.7
        else:
            confidence = 0.55

        return EmailClassification(
            intent=intent,
            urgency=urgency,
            confidence=confidence,
            matched=matched + urgency_matches,
        )


def _classify_intent(haystack: str) -> tuple[str, list[str]]:
    for intent, needles in _INTENT_RULES:
        matched = _match_any(haystack, needles)
        if matched:
            return intent, matched
    return INTENT_UNKNOWN, []


def _classify_urgency(subject: str, body_text: str) -> tuple[str, list[str]]:
    # Urgency cues in the subject are weighted more than body cues: a
    # subject "URGENT: delayed shipment" is a stronger signal than the
    # word "urgent" appearing in a long body.
    subject_lower = subject.lower()
    body_lower = body_text.lower()

    for urgency, needles in _URGENCY_RULES:
        subject_matches = _match_any(subject_lower, needles)
        if subject_matches:
            return urgency, [f"subject:{m}" for m in subject_matches]

    for urgency, needles in _URGENCY_RULES:
        body_matches = _match_any(body_lower, needles)
        if body_matches:
            return urgency, [f"body:{m}" for m in body_matches]

    return URGENCY_NORMAL, []


def _looks_internal(from_address: str, to_addresses: list[str]) -> bool:
    """
    Return True if the sender's domain equals any recipient's domain.
    Empty/malformed addresses return False.
    """
    if not from_address or "@" not in from_address:
        return False
    from_domain = from_address.rsplit("@", 1)[-1].lower()
    if not from_domain:
        return False
    for addr in to_addresses:
        if not addr or "@" not in addr:
            continue
        if addr.rsplit("@", 1)[-1].lower() == from_domain:
            return True
    return False


# --- injection point -------------------------------------------------------

_CLASSIFIER: EmailClassifier = KeywordClassifier()


def get_classifier() -> EmailClassifier:
    """
    Return the process-wide classifier.

    When an LLM-backed classifier is added, this is the only function that
    changes. Call sites already depend on the Protocol, not the concrete
    class, so no call site needs to know.
    """
    return _CLASSIFIER
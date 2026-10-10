# app/services/email/summary.py
"""
Inbox aggregation over ingested email Documents.

Returns a frozen EmailSummary describing what was received in a rolling
window. This is intentionally the *only* thing v1 reports about inbox
state, because it is the only thing we can compute truthfully today:

  - we know what arrived (each inbound email is a Document)
  - we know intent and urgency (classifier output stored in doc_metadata)
  - we do NOT know whether an email was answered

"Unanswered" is deliberately absent. Until replies are reliably matched
back to their parent messages, reporting an "unanswered" count would
imply knowledge the system does not have. When send_email begins
recording a link from sent message -> parent message, this module gains
an answered_at lookup and the summary can grow that field. Not before.

Windowing is in rolling hours, not since-midnight. The caller (or the
planner, or the LLM) is responsible for choosing a sensible window; this
module enforces only the bounds (1 <= hours <= 720, i.e. up to 30 days).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge import Document

DEFAULT_WINDOW_HOURS = 24
MIN_WINDOW_HOURS = 1
MAX_WINDOW_HOURS = 720  # 30 days

TOP_SENDERS_LIMIT = 5
HIGH_URGENCY_LIMIT = 10

# Intents and urgencies we count. Order matters for deterministic output.
_KNOWN_INTENTS = (
    "complaint", "inquiry", "order", "invoice",
    "support", "feedback", "internal", "unknown",
)
_KNOWN_URGENCIES = ("low", "normal", "high", "urgent")

# Urgencies that count as "high or urgent" in the headline text.
_ATTENTION_URGENCIES = frozenset({"high", "urgent"})


@dataclass(frozen=True)
class HighUrgencyMessage:
    message_id: str
    from_address: str
    from_name: str | None
    subject: str
    received_at: str  # ISO
    urgency: str
    intent: str


@dataclass(frozen=True)
class EmailSummary:
    window_hours: int
    total: int
    by_intent: dict[str, int] = field(default_factory=dict)
    by_urgency: dict[str, int] = field(default_factory=dict)
    top_senders: list[dict] = field(default_factory=list)
    high_urgency_messages: list[HighUrgencyMessage] = field(default_factory=list)

    def attention_count(self) -> int:
        """Count of high + urgent messages."""
        return sum(self.by_urgency.get(u, 0) for u in _ATTENTION_URGENCIES)

    def to_evidence_data(self) -> dict:
        """
        Structured payload for the EvidenceItem. Must be JSON-serializable.
        """
        return {
            "window_hours": self.window_hours,
            "total": self.total,
            "by_intent": dict(self.by_intent),
            "by_urgency": dict(self.by_urgency),
            "attention_count": self.attention_count(),
            "top_senders": list(self.top_senders),
            "high_urgency_messages": [
                {
                    "message_id": m.message_id,
                    "from_address": m.from_address,
                    "from_name": m.from_name,
                    "subject": m.subject,
                    "received_at": m.received_at,
                    "urgency": m.urgency,
                    "intent": m.intent,
                }
                for m in self.high_urgency_messages
            ],
        }

    def to_evidence_text(self) -> str:
        """
        Human-readable one-paragraph summary for the LLM prompt. Honest
        about uncertainty: says "received" and "high or urgent"; does not
        claim "unanswered" or "open".
        """
        if self.total == 0:
            return (
                f"No emails received in the last {self.window_hours} hour(s)."
            )
        parts: list[str] = []
        parts.append(
            f"{self.total} email(s) received in the last "
            f"{self.window_hours} hour(s)."
        )
        intent_bits = [
            f"{self.by_intent[i]} {i}"
            for i in _KNOWN_INTENTS
            if self.by_intent.get(i, 0) > 0
        ]
        if intent_bits:
            parts.append("By intent: " + ", ".join(intent_bits) + ".")
        urgency_bits = [
            f"{self.by_urgency[u]} {u}"
            for u in _KNOWN_URGENCIES
            if self.by_urgency.get(u, 0) > 0
        ]
        if urgency_bits:
            parts.append("By urgency: " + ", ".join(urgency_bits) + ".")
        attention = self.attention_count()
        if attention:
            parts.append(f"{attention} high or urgent.")
        if self.top_senders:
            senders = ", ".join(
                f"{s['from_address']} ({s['count']})" for s in self.top_senders
            )
            parts.append(f"Top senders: {senders}.")
        return " ".join(parts)


def _validate_window(window_hours: int) -> int:
    if not isinstance(window_hours, int):
        raise ValueError(
            f"window_hours must be int, got {type(window_hours).__name__}"
        )
    if window_hours < MIN_WINDOW_HOURS or window_hours > MAX_WINDOW_HOURS:
        raise ValueError(
            f"window_hours must be between {MIN_WINDOW_HOURS} and "
            f"{MAX_WINDOW_HOURS}, got {window_hours}"
        )
    return window_hours


def _parse_received_at(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


async def summarize_inbox(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_ids: list[uuid.UUID] | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> EmailSummary:
    """
    Aggregate inbound email over the last `window_hours` hours.

    Filtering:
      - tenant_id (mandatory)
      - source_ids (optional, when the request targets a subset)
      - content_type='email'
      - status='ready'  (skip documents still chunking or failed)
      - received_at within window (parsed from doc_metadata)

    All counting is done in Python. Volumes here are emails per tenant,
    not rows per table, so this is fine at realistic scale (hundreds per
    day). A future migration can add a received_at column on Document
    for indexed range queries if this ever becomes a hotspot.
    """
    window_hours = _validate_window(window_hours)
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)

    stmt = select(Document).where(
        Document.tenant_id == tenant_id,
        Document.content_type == "email",
        Document.status == "ready",
    )
    if source_ids:
        stmt = stmt.where(Document.source_id.in_(source_ids))

    docs = list((await db.execute(stmt)).scalars().all())

    by_intent: dict[str, int] = {i: 0 for i in _KNOWN_INTENTS}
    by_urgency: dict[str, int] = {u: 0 for u in _KNOWN_URGENCIES}
    sender_counts: dict[str, int] = {}
    high_urgency: list[HighUrgencyMessage] = []
    total = 0

    for doc in docs:
        meta = doc.doc_metadata or {}
        received_at = _parse_received_at(meta.get("received_at"))
        if received_at is None or received_at < cutoff:
            continue

        total += 1
        intent_value = meta.get("intent")
        urgency_value = meta.get("urgency")
        intent = intent_value if isinstance(intent_value, str) and intent_value else "unknown"
        urgency = urgency_value if isinstance(urgency_value, str) and urgency_value else "normal"
        if intent not in by_intent:
            by_intent[intent] = 0
        if urgency not in by_urgency:
            by_urgency[urgency] = 0
        by_intent[intent] += 1
        by_urgency[urgency] += 1

        from_address = meta.get("from_address")
        if isinstance(from_address, str) and from_address:
            sender_counts[from_address] = sender_counts.get(from_address, 0) + 1

        if urgency in _ATTENTION_URGENCIES:
            high_urgency.append(HighUrgencyMessage(
                message_id=str(meta.get("message_id") or ""),
                from_address=str(from_address or ""),
                from_name=meta.get("from_name") if isinstance(meta.get("from_name"), str) else None,
                subject=str(meta.get("subject") or doc.title),
                received_at=received_at.isoformat(),
                urgency=urgency,
                intent=intent,
            ))

    # Deterministic ordering: highest urgency first, then most recent.
    _urgency_rank = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    high_urgency.sort(
        key=lambda m: (_urgency_rank.get(m.urgency, 9), -_iso_key(m.received_at))
    )
    high_urgency = high_urgency[:HIGH_URGENCY_LIMIT]

    top_senders = [
        {"from_address": address, "count": count}
        for address, count in sorted(
            sender_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:TOP_SENDERS_LIMIT]
    ]

    return EmailSummary(
        window_hours=window_hours,
        total=total,
        by_intent=by_intent,
        by_urgency=by_urgency,
        top_senders=top_senders,
        high_urgency_messages=high_urgency,
    )


def _iso_key(iso: str) -> float:
    """
    Sort key for ISO strings. Newest = largest. Malformed input sorts last.
    """
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0
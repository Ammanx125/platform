# app/services/orchestration/policies.py
"""
Policy checks applied to orchestrator output.

Step 10 validates claims against the evidence package. Tool-call
authorization is Step 11's responsibility.
"""
from __future__ import annotations

from app.services.llm.schemas import Claim
from app.services.orchestration.context import Evidence, ValidatedClaim


def validate_claims(
    claims: list[Claim], evidence: Evidence
) -> tuple[list[ValidatedClaim], list[dict]]:
    """
    Drop claims that cite evidence ids not present in the package, or that
    cite no evidence at all.

    Returns (validated, dropped) where dropped carries the reason so the
    audit trail can show what the LLM said but couldn't back up.
    """
    valid_ids: set[str] = {item.id for item in evidence.all_items()}
    validated: list[ValidatedClaim] = []
    dropped: list[dict] = []

    for c in claims:
        bad = [eid for eid in c.evidence_ids if eid not in valid_ids]
        if bad:
            dropped.append({
                "text": c.text,
                "evidence_ids": c.evidence_ids,
                "reason": f"unknown evidence ids: {bad}",
            })
            continue
        if not c.evidence_ids:
            dropped.append({
                "text": c.text,
                "evidence_ids": [],
                "reason": "no evidence cited",
            })
            continue
        validated.append(ValidatedClaim(text=c.text, evidence_ids=list(c.evidence_ids)))

    return validated, dropped
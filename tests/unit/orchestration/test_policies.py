# tests/unit/orchestration/test_policies.py
from app.services.llm.schemas import Claim
from app.services.orchestration.context import Evidence, EvidenceItem
from app.services.orchestration.policies import validate_claims


def _evidence_with(items: list[EvidenceItem]) -> Evidence:
    e = Evidence()
    e.chunks = items
    return e


def test_keeps_claim_with_valid_evidence():
    e = _evidence_with([EvidenceItem(
        kind="chunk", id="chunk-1", text="t", data={"id": "chunk-1"},
    )])
    kept, dropped = validate_claims(
        [Claim(text="x", evidence_ids=["chunk-1"])], e
    )
    assert len(kept) == 1
    assert dropped == []


def test_drops_claim_with_unknown_evidence():
    e = _evidence_with([EvidenceItem(
        kind="chunk", id="chunk-1", text="t", data={},
    )])
    kept, dropped = validate_claims(
        [Claim(text="x", evidence_ids=["chunk-999"])], e
    )
    assert kept == []
    assert len(dropped) == 1
    assert "unknown" in dropped[0]["reason"]


def test_drops_claim_with_no_evidence():
    e = _evidence_with([])
    kept, dropped = validate_claims([Claim(text="x", evidence_ids=[])], e)
    assert kept == []
    assert dropped[0]["reason"] == "no evidence cited"
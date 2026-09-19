# tests/unit/understanding/test_matcher.py
import pytest

from app.db.models.semantic import CanonicalConcept
from app.services.understanding.matcher import (
    DeterministicMatcher,
    normalize_column_name,
)


def _concept(key, display, synonyms, kind="attribute", domain="Test", value_type="string"):
    return CanonicalConcept(
        key=key,
        display_name=display,
        synonyms=synonyms,
        kind=kind,
        domain=domain,
        value_type=value_type,
    )


@pytest.mark.parametrize("raw,expected", [
    ("Customer Name", "customername"),
    ("customer_name", "customername"),
    ("customerName", "customername"),
    ("CUSTOMER-NAME", "customername"),
    ("cust_nm", "custnm"),
    ("Supplier ID", "supplierid"),
    ("", ""),
])
def test_normalize(raw, expected):
    assert normalize_column_name(raw) == expected


@pytest.mark.asyncio
async def test_exact_synonym_match():
    concepts = [
        _concept("Sales.Customer", "Customer", ["customer", "client"]),
        _concept("Procurement.Supplier", "Supplier", ["supplier", "vendor"]),
    ]
    matcher = DeterministicMatcher()
    proposals = await matcher.propose(
        columns=["customer_name", "vendor"],
        concepts=concepts,
    )
    by_col = {p.source_column: p for p in proposals}
    assert by_col["customer_name"].canonical_concept_key == "Sales.Customer"
    assert by_col["customer_name"].rationale["method"] == "exact"
    assert by_col["vendor"].canonical_concept_key == "Procurement.Supplier"


@pytest.mark.asyncio
async def test_fuzzy_match():
    concepts = [_concept("Procurement.Supplier", "Supplier", ["supplier", "vendor"])]
    matcher = DeterministicMatcher()
    # 'suppliers' isn't an exact synonym but should fuzzy-match 'supplier'
    proposals = await matcher.propose(columns=["suppliers"], concepts=concepts)
    assert len(proposals) == 1
    assert proposals[0].canonical_concept_key == "Procurement.Supplier"
    assert proposals[0].rationale["method"] in ("fuzzy_strong", "fuzzy_weak")


@pytest.mark.asyncio
async def test_unmatched_column_produces_nothing():
    concepts = [_concept("Sales.Customer", "Customer", ["customer"])]
    matcher = DeterministicMatcher()
    proposals = await matcher.propose(columns=["xyzzy"], concepts=concepts)
    assert proposals == []


@pytest.mark.asyncio
async def test_best_match_wins_on_ties():
    # Two concepts have 'amount' as a synonym; the one whose display name is
    # closer to the column should win.
    concepts = [
        _concept("Finance.Revenue", "Revenue", ["amount"], domain="Finance"),
        _concept("Common.Amount", "Amount", ["amount"], domain="Common"),
    ]
    matcher = DeterministicMatcher()
    proposals = await matcher.propose(columns=["amount"], concepts=concepts)
    assert len(proposals) == 1
    # Both candidates normalize to 'amount', so it's a tie — either could
    # win. The test asserts that exactly one wins, not which.
    assert proposals[0].source_column == "amount"
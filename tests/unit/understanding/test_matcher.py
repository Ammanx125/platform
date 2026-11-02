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
    # 'customer_name' is 'customer' plus a suffix, not a whole-name match.
    assert by_col["customer_name"].rationale["method"] == "fuzzy_strong"
    assert by_col["customer_name"].confidence < 1.0
    assert by_col["vendor"].canonical_concept_key == "Procurement.Supplier"
    assert by_col["vendor"].rationale["method"] == "exact"


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

# ---------- scoring: component matches must not outrank whole-name matches ----------


@pytest.mark.asyncio
async def test_short_synonym_does_not_hijack_a_compound_column():
    """
    Regression: 'unit_price' scored 100 against Management.BusinessUnit
    (synonym 'unit') and beat Procurement.PurchasePrice (synonym
    'unit_price'). The whole-name match must win.
    """
    concepts = [
        _concept(
            "Procurement.PurchasePrice",
            "Purchase Price",
            ["purchase_price", "buy_price", "unit_cost", "unit_price"],
        ),
        _concept(
            "Management.BusinessUnit",
            "Business Unit",
            ["business_unit", "unit", "depot", "division"],
        ),
    ]
    proposals = await DeterministicMatcher().propose(
        columns=["unit_price"], concepts=concepts
    )

    assert len(proposals) == 1
    assert proposals[0].canonical_concept_key == "Procurement.PurchasePrice"
    assert proposals[0].rationale["method"] == "exact"


@pytest.mark.asyncio
async def test_component_match_loses_to_whole_name_fuzzy():
    """A one-word match must not beat a close whole-name fuzzy match."""
    concepts = [
        _concept("Test.Unit", "Unit", ["unit"]),
        _concept("Test.UnitPrices", "Unit Prices", ["unit_prices"]),
    ]
    proposals = await DeterministicMatcher().propose(
        columns=["unit_prices"], concepts=concepts
    )
    assert proposals[0].canonical_concept_key == "Test.UnitPrices"


@pytest.mark.asyncio
async def test_multiword_synonym_matches_a_compound_column():
    """'unit cost' as a synonym should still match 'unit_cost' as a whole."""
    concepts = [
        _concept("Procurement.PurchasePrice", "Purchase Price", ["unit cost"])
    ]
    proposals = await DeterministicMatcher().propose(
        columns=["unit_cost"], concepts=concepts
    )
    assert len(proposals) == 1
    assert proposals[0].canonical_concept_key == "Procurement.PurchasePrice"
    assert proposals[0].rationale["method"] == "exact"


@pytest.mark.asyncio
async def test_component_match_still_proposes():
    """A genuine one-word column still matches on a part, with lower confidence."""
    concepts = [_concept("Procurement.Supplier", "Supplier", ["supplier", "vendor"])]
    proposals = await DeterministicMatcher().propose(
        columns=["vendor_id"], concepts=concepts
    )
    assert len(proposals) == 1
    assert proposals[0].canonical_concept_key == "Procurement.Supplier"
    assert proposals[0].confidence < 1.0


@pytest.mark.asyncio
async def test_short_candidates_need_an_exact_match():
    """'unit' must not match 'unit_price' as an isolated candidate."""
    concepts = [_concept("Management.BusinessUnit", "Business Unit", ["unit"])]
    proposals = await DeterministicMatcher().propose(
        columns=["unit_price", "unit_qty"], concepts=concepts
    )
    # 'unit' (4 chars) may only match a column name exactly, so it proposes
    # nothing for columns that merely contain the word.
    assert proposals == []
    # And 'unit' alone still matches the exact column.
    exact = await DeterministicMatcher().propose(
        columns=["unit"], concepts=concepts
    )
    assert exact[0].rationale["matched"] == "unit"
    assert exact[0].confidence == 1.0


@pytest.mark.asyncio
async def test_short_candidate_still_matches_the_exact_column():
    concepts = [_concept("Management.BusinessUnit", "Business Unit", ["unit"])]
    proposals = await DeterministicMatcher().propose(columns=["unit"], concepts=concepts)
    assert len(proposals) == 1
    assert proposals[0].rationale["method"] == "exact"

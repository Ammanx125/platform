# tests/unit/services/semantic/test_concept_metadata.py
import pytest

from app.db.seed_concepts import (
    ALL_CONCEPTS,
    KNOWN_UNITS,
    _validate_metadata,
)
from app.db.seed_packs import PACKS

# Global concepts describe the measure, but not a tenant's currency.
MONETARY_CONCEPTS = {
    "Procurement.PurchasePrice",
    "Finance.Revenue",
    "Finance.Expense",
    "Finance.Cost",
    "Finance.CostOfGoodsSold",
    "Finance.Payment",
    "Sales.SellingPrice",
    "Operations.ProductionCost",
    "Management.Budget",
    "Common.Amount",
}


def _by_key():
    return {concept[0]: concept for concept in ALL_CONCEPTS}


def test_every_seeded_concept_has_a_display_name():
    for concept in ALL_CONCEPTS:
        assert concept[1], f"{concept[0]} has no display_name"


def test_every_seeded_concept_has_a_description():
    for concept in ALL_CONCEPTS:
        assert concept[6], f"{concept[0]} has no description"


def test_synonyms_are_lists_of_strings():
    for concept in ALL_CONCEPTS:
        synonyms = concept[5]
        assert isinstance(synonyms, list)
        assert all(isinstance(s, str) and s for s in synonyms)


def test_declared_units_are_known():
    for concept in ALL_CONCEPTS:
        unit = concept[7]
        if unit is not None:
            assert unit in KNOWN_UNITS, f"{concept[0]} declares {unit!r}"


@pytest.mark.parametrize("key", sorted(MONETARY_CONCEPTS))
def test_monetary_concepts_declare_currency_measure_without_denomination(key):
    concept = _by_key()[key]
    assert concept[7] == "currency"
    assert concept[8] is None


def test_only_currency_units_declare_a_currency():
    for concept in ALL_CONCEPTS:
        unit, currency = concept[7], concept[8]
        if unit != "currency":
            assert currency is None, f"{concept[0]} has currency without unit"


def test_non_quantity_concepts_have_no_unit():
    """Entities and identifiers are not measurable, so they stay unlabelled."""
    for key in ("Procurement.Supplier", "Inventory.SKU", "Sales.Customer"):
        assert _by_key()[key][7] is None


def test_validate_metadata_accepts_the_catalog():
    _validate_metadata()  # does not raise


@pytest.mark.parametrize(
    "bad",
    [
        # currency declared on a non-currency unit
        ("X.B", "B", "X", "attribute", "number", [], "d", "count", "ETB"),
        # unknown unit
        ("X.C", "C", "X", "attribute", "number", [], "d", "furlongs", None),
        # missing display name
        ("X.D", "", "X", "attribute", "number", [], "d", None, None),
    ],
)
def test_validate_metadata_rejects_incomplete_concepts(monkeypatch, bad):
    from app.db import seed_concepts

    monkeypatch.setattr(seed_concepts, "ALL_CONCEPTS", [bad])
    with pytest.raises(ValueError):
        seed_concepts._validate_metadata()


def test_currency_denomination_is_optional_but_valid_when_declared(monkeypatch):
    from app.db import seed_concepts

    monetary_concept = (
        "X.A",
        "A",
        "X",
        "attribute",
        "number",
        [],
        "d",
        "currency",
        "USD",
    )
    monkeypatch.setattr(seed_concepts, "ALL_CONCEPTS", [monetary_concept])

    seed_concepts._validate_metadata()


def test_fuel_use_rate_has_a_composite_rate_unit():
    concept = _by_key().get("Transport.FuelUseRate")
    if concept is None:  # lives in the transport pack
        pack = next(p for p in PACKS if p["key"] == "transport.v1")
        concept_spec = next(c for c in pack["concepts"] if c["key"] == "Transport.FuelUseRate")
        assert concept_spec["unit"] == "litres_per_100km"
        return
    assert concept[7] == "litres_per_100km"


def test_transport_pack_concepts_all_declare_their_measure():
    pack = next(p for p in PACKS if p["key"] == "transport.v1")
    for concept in pack["concepts"]:
        assert "unit" in concept, f"{concept['key']} does not declare a unit"
        assert concept["unit"] in KNOWN_UNITS | {None}

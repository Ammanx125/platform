# tests/unit/workflows/test_registry.py
from app.services.workflows.registry import get, keys, match_by_query


def test_all_workflows_registered():
    import app.services.workflows  # noqa: F401 — triggers template registration
    keys_list = keys()
    assert "procurement.spend_analysis" in keys_list
    assert "procurement.supplier_reorder" in keys_list
    assert "inventory.stock_analysis" in keys_list


def test_get_unknown_returns_none():
    assert get("does.not.exist") is None


def test_match_by_query_finds_procurement():
    import app.services.workflows  # noqa: F401
    matches = match_by_query("why did procurement spend increase")
    assert "procurement.spend_analysis" in matches


def test_match_by_query_no_match():
    import app.services.workflows  # noqa: F401
    assert match_by_query("the weather is nice") == []
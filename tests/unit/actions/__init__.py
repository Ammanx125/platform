# tests/unit/actions/test_registry.py
from app.services.actions import registry


def test_internal_tools_are_registered():
    # Importing app.services.actions registers them.
    import app.services.actions  # noqa: F401
    names = set(registry.names())
    assert {"list_suppliers", "get_supplier_detail",
            "list_recent_anomalies", "generate_report"}.issubset(names)


def test_tool_schemas_shape():
    import app.services.actions  # noqa: F401
    schemas = registry.tool_schemas()
    assert schemas
    for s in schemas:
        assert s["type"] == "function"
        assert "name" in s["function"]
        assert "description" in s["function"]
        assert "parameters" in s["function"]


def test_get_unknown_returns_none():
    assert registry.get("does_not_exist") is None
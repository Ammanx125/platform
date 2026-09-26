# tests/unit/llm/test_validation.py
import pytest

from app.services.llm.base import LLMProviderError
from app.services.llm.validation import validate_response


def _valid_raw() -> dict:
    return {
        "summary": "Revenue rose 12% QoQ.",
        "claims": [
            {"text": "Revenue rose 12%.", "evidence_ids": ["chunk-1"]},
        ],
        "recommended_actions": [
            {"description": "Review pricing.", "suggested_tool": None, "rationale": None},
        ],
        "tool_calls": [
            {
                "name": "create_purchase_order",
                "arguments": {"supplier_id": "S1", "quantity": 10},
                "rationale": "Low stock",
            },
        ],
    }


def test_valid_response():
    r = validate_response(_valid_raw(), provider="mock", model_name="mock")
    assert r.summary.startswith("Revenue")
    assert len(r.claims) == 1
    assert r.claims[0].text == "Revenue rose 12%."
    assert r.tool_calls[0].name == "create_purchase_order"
    assert r.provider == "mock"


def test_rejects_non_dict():
    with pytest.raises(LLMProviderError, match="must be an object"):
        validate_response("not a dict", provider="p", model_name="m")


def test_rejects_missing_summary():
    raw = _valid_raw()
    del raw["summary"]
    with pytest.raises(LLMProviderError, match="summary"):
        validate_response(raw, provider="p", model_name="m")


def test_rejects_non_string_summary():
    raw = _valid_raw()
    raw["summary"] = 42
    with pytest.raises(LLMProviderError, match="summary"):
        validate_response(raw, provider="p", model_name="m")


def test_tolerates_missing_optional_fields():
    r = validate_response({"summary": "ok"}, provider="p", model_name="m")
    assert r.claims == []
    assert r.tool_calls == []


def test_rejects_tool_call_without_name():
    raw = _valid_raw()
    raw["tool_calls"] = [{"arguments": {"x": 1}}]
    with pytest.raises(LLMProviderError, match="name"):
        validate_response(raw, provider="p", model_name="m")


def test_rejects_tool_call_with_non_dict_arguments():
    raw = _valid_raw()
    raw["tool_calls"] = [{"name": "foo", "arguments": "not a dict"}]
    with pytest.raises(LLMProviderError, match="arguments"):
        validate_response(raw, provider="p", model_name="m")


def test_rejects_unknown_tool_is_NOT_validated_here():
    """
    Semantic validation is the orchestrator's job. The provider's validator
    must accept any structurally valid tool name, even one that Sansa has
    never heard of.
    """
    raw = _valid_raw()
    raw["tool_calls"] = [{"name": "not_a_real_tool", "arguments": {}}]
    r = validate_response(raw, provider="p", model_name="m")
    assert r.tool_calls[0].name == "not_a_real_tool"


def test_rejects_non_list_claims():
    raw = _valid_raw()
    raw["claims"] = "not a list"
    with pytest.raises(LLMProviderError, match="claims must be a list"):
        validate_response(raw, provider="p", model_name="m")


def test_rejects_claim_with_bad_evidence_ids():
    raw = _valid_raw()
    raw["claims"] = [{"text": "x", "evidence_ids": [1, 2]}]
    with pytest.raises(LLMProviderError, match="evidence_ids"):
        validate_response(raw, provider="p", model_name="m")
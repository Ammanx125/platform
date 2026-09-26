# tests/unit/security/test_redaction.py
from app.services.security.redaction import redact, redact_dict


def test_bearer_token_redacted():
    out = redact("Authorization: Bearer abc123def456")
    assert "abc123def456" not in out
    assert "Bearer [REDACTED]" in out


def test_jwt_redacted():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature_part_here"
    out = redact(f"token={jwt}")
    assert jwt not in out
    assert "[REDACTED JWT]" in out


def test_key_value_redacted():
    out = redact("api_key=supersecretvalue123")
    assert "supersecretvalue123" not in out
    assert "[REDACTED]" in out


def test_openai_style_key():
    out = redact("sk-abcdefghijklmnopqrstuvwxyz1234")
    assert "abcdefghijklmnopqrstuvwxyz1234" not in out
    assert "[REDACTED KEY]" in out


def test_normal_text_unchanged():
    text = "The supplier delivered 100 units on time."
    assert redact(text) == text


def test_redact_is_idempotent():
    once = redact("Bearer abc123xyz")
    twice = redact(once)
    assert once == twice


def test_redact_dict_recurses():
    obj = {"outer": {"token": "Bearer abc123"}, "list": ["api_key=xyz"]}
    out = redact_dict(obj)
    assert "abc123" not in str(out)
    assert "xyz" not in str(out)


def test_redact_dict_preserves_non_strings():
    obj = {"n": 42, "f": 3.14, "b": True, "x": None}
    assert redact_dict(obj) == obj
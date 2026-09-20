# tests/unit/ingestion/test_webhook_signature.py
from app.services.ingestion import webhook


def test_signature_round_trip() -> None:
    body = b'{"event":"purchase","amount":42}'
    secret = "test-secret-value"
    sig = webhook.compute_signature(raw_body=body, secret=secret)
    assert webhook.verify_signature(raw_body=body, secret=secret, provided=sig)


def test_signature_rejects_wrong_secret() -> None:
    body = b'{"a":1}'
    sig = webhook.compute_signature(raw_body=body, secret="right")
    assert not webhook.verify_signature(raw_body=body, secret="wrong", provided=sig)


def test_signature_rejects_modified_body() -> None:
    body = b'{"a":1}'
    sig = webhook.compute_signature(raw_body=body, secret="s")
    assert not webhook.verify_signature(
        raw_body=b'{"a":2}', secret="s", provided=sig
    )
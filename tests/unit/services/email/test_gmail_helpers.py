# tests/unit/services/email/test_gmail_helpers.py
from __future__ import annotations

import base64

from app.services.email.gmail import (
    _decode_body,
    _parse_address,
    _parse_address_list,
)


def test_parse_address_with_name():
    assert _parse_address("Foo Bar <Foo@Example.com>") == ("foo@example.com", "Foo Bar")


def test_parse_address_bare():
    assert _parse_address("foo@example.com") == ("foo@example.com", None)


def test_parse_address_quoted():
    assert _parse_address('"Foo, Bar" <foo@example.com>') == ("foo@example.com", "Foo, Bar")


def test_parse_address_empty():
    assert _parse_address(None) == ("", None)


def test_parse_address_list():
    header = "a@x.example, B <b@y.example>, c@z.example"
    assert _parse_address_list(header) == ["a@x.example", "b@y.example", "c@z.example"]


def test_parse_address_list_handles_commas_in_display_names():
    header = '"Doe, John" <john@x.example>, jane@y.example'
    assert _parse_address_list(header) == ["john@x.example", "jane@y.example"]


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def test_decode_body_plain():
    payload = {
        "mimeType": "text/plain",
        "body": {"data": _b64("hello world")},
    }
    assert _decode_body(payload) == "hello world"


def test_decode_body_html_fallback():
    html = "<p>hello <b>world</b></p>"
    payload = {
        "mimeType": "text/html",
        "body": {"data": _b64(html)},
    }
    out = _decode_body(payload)
    assert "hello" in out and "world" in out and "<" not in out


def test_decode_body_multipart_prefers_plain():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("plain text")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
        ],
    }
    assert _decode_body(payload) == "plain text"


def test_decode_body_empty():
    assert _decode_body({"mimeType": "text/plain"}) == ""
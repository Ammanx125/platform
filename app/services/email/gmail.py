# app/services/email/gmail.py
"""
Gmail provider.

Implements EmailProvider against Google's Gmail REST API. Uses
google-auth for OAuth and google-api-python-client for the API calls.
All HTTP is synchronous under the hood; the async surface here wraps
calls in asyncio.to_thread so we don't block the event loop.

Scope requirements (configurable in settings.google_oauth_scopes):
  - https://www.googleapis.com/auth/gmail.readonly
  - https://www.googleapis.com/auth/gmail.compose

gmail.compose is what lets us create drafts AND send messages that our
app created. It does not grant reading arbitrary messages (that's
gmail.readonly) and does not grant sending messages that someone else
composed (that's gmail.send, which we do not request).
"""
from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from email.mime.text import MIMEText
from email.utils import getaddresses
from typing import Any

from app.core.config import settings
from app.services.email.base import (
    DraftResult,
    EmailAuthError,
    EmailProviderError,
    FetchedEmail,
    OAuthTokenSet,
    SendResult,
)

# Lazy imports so environments without google deps don't crash on import.
# provider_registry.py wraps the import in try/except.
try:
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build as google_build
    from googleapiclient.errors import HttpError
except ImportError as _exc:  # pragma: no cover - tested via provider_registry
    raise ImportError(
        "GmailProvider requires google-auth and google-api-python-client; "
        "install with: pip install google-auth google-auth-oauthlib "
        "google-api-python-client"
    ) from _exc


_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_REVOKE_URI = "https://oauth2.googleapis.com/revoke"


def _require_client_config() -> tuple[str, str]:
    cid = settings.google_oauth_client_id
    csecret = settings.google_oauth_client_secret
    if not cid or not csecret:
        raise EmailAuthError(
            "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be set"
        )
    return cid, csecret


def _credentials_from_tokens(tokens: OAuthTokenSet) -> Credentials:
    cid, csecret = _require_client_config()
    return Credentials(
        token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        token_uri=_TOKEN_URI,
        client_id=cid,
        client_secret=csecret,
        scopes=tokens.scope.split() if tokens.scope else None,
    )


def _tokens_from_credentials(
    creds: Credentials, *, previous_refresh_token: str | None = None
) -> OAuthTokenSet:
    expires_at = creds.expiry
    if expires_at is None:
        # Google always returns expires_in; if not, assume 1h for safety.
        expires_at = datetime.now(UTC) + timedelta(hours=1)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return OAuthTokenSet(
        access_token=creds.token or "",
        refresh_token=creds.refresh_token or previous_refresh_token,
        expires_at=expires_at,
        scope=" ".join(creds.scopes or []),
        token_type="Bearer",
    )


def _decode_body(payload: dict[str, Any]) -> str:
    """
    Walk a Gmail message payload looking for text/plain. Falls back to
    text/html with tags stripped (naive — real HTML-to-text is a rabbit
    hole; this is enough for classification, and the raw text stays
    available via the Document's raw_text if needed).

    Returns "" if no decodable text body is found (e.g. attachment-only).
    """
    import re

    def _decode_b64(data: str) -> str:
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")

    def _strip_html(html: str) -> str:
        # Remove script/style blocks first, then tags, then collapse ws.
        html = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", html, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _walk(part: dict[str, Any]) -> tuple[str, str] | None:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if mime == "text/plain" and data:
            return ("plain", _decode_b64(data))
        if mime == "text/html" and data:
            return ("html", _decode_b64(data))
        for sub in part.get("parts", []) or []:
            found = _walk(sub)
            if found is not None:
                # Prefer plain over html at the first match.
                if found[0] == "plain":
                    return found
        return None

    found = _walk(payload)
    if found is None:
        return ""
    if found[0] == "plain":
        return found[1]
    return _strip_html(found[1])


def _header(headers: list[dict[str, str]], name: str) -> str | None:
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value")
    return None


def _parse_address(header_value: str | None) -> tuple[str, str | None]:
    """
    Parse a From header into (address, display_name).

    Handles the common forms:
        "Foo Bar <foo@example.com>"
        "foo@example.com"
        "Foo Bar" <foo@example.com>
    Never raises. On malformed input, returns (raw_stripped, None).
    """
    if not header_value:
        return ("", None)
    import re

    m = re.match(r"^\s*(.*?)\s*<([^>]+)>\s*$", header_value)
    if m:
        display, addr = m.group(1).strip().strip('"'), m.group(2).strip()
        return (addr.lower(), display or None)
    return (header_value.strip().lower(), None)


def _parse_address_list(header_value: str | None) -> list[str]:
    if not header_value:
        return []
    return [
        address.strip().lower()
        for _, address in getaddresses([header_value])
        if address.strip()
    ]


class GmailProvider:
    name = "gmail"

    # ---------- OAuth ----------

    async def build_authorize_url(
        self, *, state: str, redirect_uri: str
    ) -> str:
        cid, _ = _require_client_config()
        scopes = " ".join(settings.google_oauth_scopes)
        from urllib.parse import urlencode

        params = {
            "client_id": cid,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes,
            "state": state,
            "access_type": "offline",
            "prompt": "consent",  # force refresh_token issuance
            "include_granted_scopes": "true",
        }
        return f"{_AUTH_URI}?{urlencode(params)}"

    async def exchange_code(
        self, *, code: str, redirect_uri: str
    ) -> OAuthTokenSet:
        return await asyncio.to_thread(self._exchange_code_sync, code, redirect_uri)

    def _exchange_code_sync(self, code: str, redirect_uri: str) -> OAuthTokenSet:
        cid, csecret = _require_client_config()
        from google_auth_oauthlib.flow import Flow

        client_config = {
            "web": {
                "client_id": cid,
                "client_secret": csecret,
                "auth_uri": _AUTH_URI,
                "token_uri": _TOKEN_URI,
                "redirect_uris": [redirect_uri],
            }
        }
        flow = Flow.from_client_config(client_config, scopes=None)
        flow.redirect_uri = redirect_uri
        try:
            flow.fetch_token(code=code)
        except Exception as exc:  # noqa: BLE001
            raise EmailAuthError(f"token exchange failed: {exc}") from exc
        if not isinstance(flow.credentials, Credentials):
            raise EmailAuthError("OAuth flow returned unsupported credentials")
        return _tokens_from_credentials(flow.credentials)

    async def refresh_tokens(self, *, tokens: OAuthTokenSet) -> OAuthTokenSet:
        return await asyncio.to_thread(self._refresh_sync, tokens)

    def _refresh_sync(self, tokens: OAuthTokenSet) -> OAuthTokenSet:
        creds = _credentials_from_tokens(tokens)
        try:
            creds.refresh(GoogleRequest())
        except Exception as exc:  # noqa: BLE001
            raise EmailAuthError(f"token refresh failed: {exc}") from exc
        # Google may not re-issue the refresh_token; preserve the old one.
        return _tokens_from_credentials(
            creds, previous_refresh_token=tokens.refresh_token
        )

    async def revoke_tokens(self, *, tokens: OAuthTokenSet) -> None:
        await asyncio.to_thread(self._revoke_sync, tokens)

    def _revoke_sync(self, tokens: OAuthTokenSet) -> None:
        import urllib.request

        target = tokens.refresh_token or tokens.access_token
        if not target:
            return
        req = urllib.request.Request(
            f"{_REVOKE_URI}?token={target}", method="POST"
        )
        try:
            urllib.request.urlopen(req, timeout=15).read()
        except Exception:  # noqa: BLE001
            # Revocation is best-effort. Token expiry bounds the damage.
            return

    # ---------- profile ----------

    async def get_profile(self, *, tokens: OAuthTokenSet) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_profile_sync, tokens)

    def _get_profile_sync(self, tokens: OAuthTokenSet) -> dict[str, Any]:
        service = self._service(tokens)
        try:
            profile = service.users().getProfile(userId="me").execute()
        except HttpError as exc:
            raise EmailProviderError(f"getProfile failed: {exc}") from exc
        return {
            "email_address": profile.get("emailAddress", "").lower(),
            "display_name": None,
            "messages_total": profile.get("messagesTotal"),
            "history_id": profile.get("historyId"),
        }

    # ---------- read ----------

    async def list_message_ids(
        self,
        *,
        tokens: OAuthTokenSet,
        cursor: str | None,
        max_messages: int,
    ) -> tuple[list[str], str | None]:
        return await asyncio.to_thread(
            self._list_message_ids_sync, tokens, cursor, max_messages
        )

    def _list_message_ids_sync(
        self,
        tokens: OAuthTokenSet,
        cursor: str | None,
        max_messages: int,
    ) -> tuple[list[str], str | None]:
        service = self._service(tokens)
        try:
            if cursor:
                # history.list returns messages added since historyId.
                hist = (
                    service.users()
                    .history()
                    .list(userId="me", startHistoryId=cursor, historyTypes=["messageAdded"])
                    .execute()
                )
                ids: list[str] = []
                for h in hist.get("history", []) or []:
                    for m in h.get("messagesAdded", []) or []:
                        mid = m.get("message", {}).get("id")
                        if mid:
                            ids.append(mid)
                ids = ids[:max_messages]
                new_cursor = str(hist.get("historyId") or cursor)
                return ids, new_cursor

            # First sync: most recent messages, newest first.
            listing = (
                service.users()
                .messages()
                .list(userId="me", maxResults=min(max_messages, 500), labelIds=["INBOX"])
                .execute()
            )
            ids = [m["id"] for m in listing.get("messages", []) or []][:max_messages]

            # Anchor cursor to the current historyId so subsequent syncs
            # are delta-based.
            profile = service.users().getProfile(userId="me").execute()
            new_cursor = str(profile.get("historyId") or "")
            return ids, new_cursor
        except HttpError as exc:
            raise EmailProviderError(f"list_message_ids failed: {exc}") from exc

    async def get_message(
        self, *, tokens: OAuthTokenSet, message_id: str
    ) -> FetchedEmail:
        return await asyncio.to_thread(self._get_message_sync, tokens, message_id)

    def _get_message_sync(
        self, tokens: OAuthTokenSet, message_id: str
    ) -> FetchedEmail:
        service = self._service(tokens)
        try:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except HttpError as exc:
            raise EmailProviderError(f"get_message failed: {exc}") from exc

        payload = msg.get("payload", {})
        headers = payload.get("headers", []) or []
        from_addr, from_name = _parse_address(_header(headers, "From"))
        to_addrs = _parse_address_list(_header(headers, "To"))
        cc_addrs = _parse_address_list(_header(headers, "Cc"))
        subject = _header(headers, "Subject") or ""

        internal_date_ms = msg.get("internalDate")
        if internal_date_ms:
            received_at = datetime.fromtimestamp(
                int(internal_date_ms) / 1000, tz=UTC
            )
        else:
            received_at = datetime.now(UTC)

        return FetchedEmail(
            message_id=msg["id"],
            thread_id=msg.get("threadId", msg["id"]),
            from_address=from_addr,
            from_name=from_name,
            to_addresses=to_addrs,
            cc_addresses=cc_addrs,
            subject=subject,
            body_text=_decode_body(payload),
            received_at=received_at,
            snippet=msg.get("snippet", ""),
            labels=list(msg.get("labelIds", []) or []),
            raw_headers={
                "history_id": msg.get("historyId"),
                "internal_date": internal_date_ms,
            },
        )

    # ---------- draft / send ----------

    async def create_draft(
        self,
        *,
        tokens: OAuthTokenSet,
        to_addresses: list[str],
        cc_addresses: list[str],
        subject: str,
        body_text: str,
        in_reply_to_message_id: str | None = None,
    ) -> DraftResult:
        return await asyncio.to_thread(
            self._create_draft_sync,
            tokens, to_addresses, cc_addresses, subject, body_text,
            in_reply_to_message_id,
        )

    def _create_draft_sync(
        self,
        tokens: OAuthTokenSet,
        to_addresses: list[str],
        cc_addresses: list[str],
        subject: str,
        body_text: str,
        in_reply_to_message_id: str | None,
    ) -> DraftResult:
        service = self._service(tokens)
        mime = MIMEText(body_text, _subtype="plain", _charset="utf-8")
        mime["To"] = ", ".join(to_addresses)
        if cc_addresses:
            mime["Cc"] = ", ".join(cc_addresses)
        mime["Subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")

        message_body: dict[str, Any] = {"raw": raw}
        if in_reply_to_message_id:
            # Thread the draft onto the parent.
            try:
                parent = (
                    service.users()
                    .messages()
                    .get(userId="me", id=in_reply_to_message_id, format="metadata")
                    .execute()
                )
                if parent.get("threadId"):
                    message_body["threadId"] = parent["threadId"]
            except HttpError:
                # Non-fatal: create an unthreaded draft rather than fail.
                pass

        try:
            draft = (
                service.users()
                .drafts()
                .create(userId="me", body={"message": message_body})
                .execute()
            )
        except HttpError as exc:
            raise EmailProviderError(f"create_draft failed: {exc}") from exc

        return DraftResult(
            draft_id=draft["id"],
            message_id=draft.get("message", {}).get("id", ""),
            thread_id=draft.get("message", {}).get("threadId", ""),
        )

    async def get_draft(
        self, *, tokens: OAuthTokenSet, draft_id: str
    ) -> DraftResult | None:
        return await asyncio.to_thread(self._get_draft_sync, tokens, draft_id)

    def _get_draft_sync(
        self, tokens: OAuthTokenSet, draft_id: str
    ) -> DraftResult | None:
        service = self._service(tokens)
        try:
            draft = (
                service.users()
                .drafts()
                .get(userId="me", id=draft_id, format="metadata")
                .execute()
            )
        except HttpError as exc:
            if getattr(exc, "resp", None) is not None and exc.resp.status == 404:
                return None
            raise EmailProviderError(f"get_draft failed: {exc}") from exc
        return DraftResult(
            draft_id=draft["id"],
            message_id=draft.get("message", {}).get("id", ""),
            thread_id=draft.get("message", {}).get("threadId", ""),
        )

    async def send_draft(
        self, *, tokens: OAuthTokenSet, draft_id: str
    ) -> SendResult:
        return await asyncio.to_thread(self._send_draft_sync, tokens, draft_id)

    def _send_draft_sync(
        self, tokens: OAuthTokenSet, draft_id: str
    ) -> SendResult:
        service = self._service(tokens)
        try:
            sent = (
                service.users()
                .drafts()
                .send(userId="me", body={"id": draft_id})
                .execute()
            )
        except HttpError as exc:
            raise EmailProviderError(f"send_draft failed: {exc}") from exc
        return SendResult(
            message_id=sent["id"],
            thread_id=sent.get("threadId", ""),
        )

    async def get_sent_message(
        self, *, tokens: OAuthTokenSet, message_id: str
    ) -> FetchedEmail | None:
        return await asyncio.to_thread(
            self._get_sent_message_sync, tokens, message_id
        )

    def _get_sent_message_sync(
        self, tokens: OAuthTokenSet, message_id: str
    ) -> FetchedEmail | None:
        service = self._service(tokens)
        try:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except HttpError as exc:
            if getattr(exc, "resp", None) is not None and exc.resp.status == 404:
                return None
            raise EmailProviderError(f"get_sent_message failed: {exc}") from exc
        # Reuse the message decoder from get_message to keep the shapes identical.
        return self._message_to_fetched(msg)

    # ---------- helpers ----------

    def _service(self, tokens: OAuthTokenSet):
        creds = _credentials_from_tokens(tokens)
        return google_build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _message_to_fetched(self, msg: dict[str, Any]) -> FetchedEmail:
        payload = msg.get("payload", {})
        headers = payload.get("headers", []) or []
        from_addr, from_name = _parse_address(_header(headers, "From"))
        to_addrs = _parse_address_list(_header(headers, "To"))
        cc_addrs = _parse_address_list(_header(headers, "Cc"))
        subject = _header(headers, "Subject") or ""
        internal_date_ms = msg.get("internalDate")
        if internal_date_ms:
            received_at = datetime.fromtimestamp(
                int(internal_date_ms) / 1000, tz=UTC
            )
        else:
            received_at = datetime.now(UTC)
        return FetchedEmail(
            message_id=msg["id"],
            thread_id=msg.get("threadId", msg["id"]),
            from_address=from_addr,
            from_name=from_name,
            to_addresses=to_addrs,
            cc_addresses=cc_addrs,
            subject=subject,
            body_text=_decode_body(payload),
            received_at=received_at,
            snippet=msg.get("snippet", ""),
            labels=list(msg.get("labelIds", []) or []),
            raw_headers={
                "history_id": msg.get("historyId"),
                "internal_date": internal_date_ms,
            },
        )
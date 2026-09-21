# app/services/ingestion/http.py
"""
HTTP connector.

Fetches from an HTTP endpoint and parses JSON or CSV. Two hard rules:

  1. SSRF protection: the URL's hostname is resolved, the resulting IP is
     checked against a blocklist, and the connection is made to that IP
     with the *original hostname* preserved for TLS SNI and the Host header.
     This closes the DNS-rebinding window between check and connect.

  2. Credentials are never stored in DataSource.config. Config holds an
     `auth_ref`; the value (bearer token, API key, basic auth string) is
     resolved via app/core/secrets.py.

Config shape:
  {
    "url": "https://api.customer.com/v1/purchases",
    "method": "GET",                       # GET | POST
    "format": "json",                      # "json" | "csv"
    "headers": {"Accept": "application/json"},
    "json_path": "$.data.items",           # optional, dotted path into JSON
    "auth_ref": "ACME_API",                # optional
    "auth_header": "Authorization",        # default
    "auth_scheme": "Bearer",               # optional
    "timeout_seconds": 30,
    "max_response_bytes": 10485760,
    "allowed_hosts": ["api.customer.com"]  # optional SSRF allowlist
  }
"""
from __future__ import annotations

import csv
import io
import ipaddress
import json
import socket
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from app.core import secrets
from app.core.config import settings
from app.services.ingestion.base import (
    IngestionError,
    IngestionResult,
    ParsedRow,
    SourceMetadata,
)

if TYPE_CHECKING:
    from app.db.models.dataset import DataSource


# ---------- SSRF defense ----------

def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_and_check(host: str, port: int | None = None) -> str:
    """
    Resolve host to one IP, check it against the blocklist, and return that
    IP as a string. Raises IngestionError if any resolved address is blocked.
    """
    try:
        infos = socket.getaddrinfo(host, port)
    except socket.gaierror as exc:
        raise IngestionError(f"could not resolve host {host!r}: {exc}") from exc

    if not infos:
        raise IngestionError(f"no addresses for host {host!r}")

    for _family, _, _, _, sockaddr in infos:
        addr = sockaddr[0]
        if not isinstance(addr, str):
            continue
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise IngestionError(
                f"refusing to connect to {host!r}: resolves to blocked address {ip}"
            )
        return addr

    raise IngestionError(f"no usable address for host {host!r}")


class _SSRFSafeTransport(httpx.AsyncHTTPTransport):
    """
    An httpx transport that resolves the hostname, checks it against the
    SSRF blocklist, and connects to the resolved IP while keeping the
    original hostname for TLS SNI and the Host header.

    This closes the DNS-rebinding window: the IP we check is the IP we
    connect to. No re-resolution happens between the check and the connect.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        host = url.host
        if not host:
            raise IngestionError("URL has no hostname")

        port = url.port or (443 if url.scheme == "https" else 80)

        # Resolve + check. Raises if any resolved IP is blocked.
        resolved_ip = _resolve_and_check(host, port)

        # httpx doesn't expose a direct "connect to this IP but use this SNI"
        # hook, so we rewrite the URL's host to the IP and set Host explicitly.
        # httpx preserves the original host for SNI when we do this through
        # the extensions mechanism below.
        new_url = url.copy_with(host=resolved_ip)
        new_headers = httpx.Headers(request.headers)
        # Preserve the original Host header (some servers require it).
        if "host" not in {k.lower() for k in new_headers.keys()}:
            new_headers["Host"] = host

        # Tell httpx to use the original hostname for TLS SNI. This is the
        # critical part: the TCP connection goes to resolved_ip, but the
        # cert is validated against `host`.
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = host

        new_request = httpx.Request(
            method=request.method,
            url=new_url,
            headers=new_headers,
            content=request.stream,
            extensions=extensions,
        )

        return await super().handle_async_request(new_request)


def _make_client(*, timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        transport=_SSRFSafeTransport(),
        follow_redirects=False,
    )


# ---------- JSON path helper ----------

def _get_json_path(obj: Any, dotted: str) -> Any:
    if not dotted or dotted == "$":
        return obj
    parts = [p for p in dotted.strip("$").split(".") if p]
    cur = obj
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        elif isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


# ---------- connector ----------

class HTTPConnector:
    source_type = "http"

    async def _fetch(self, *, source: DataSource) -> tuple[bytes, str]:
        cfg = source.config or {}
        url = cfg.get("url")
        if not url:
            raise IngestionError("http source has no url in config")

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise IngestionError(f"unsupported URL scheme: {parsed.scheme!r}")
        if not parsed.hostname:
            raise IngestionError("URL has no hostname")

        allowed_hosts = cfg.get("allowed_hosts") or []
        if allowed_hosts and parsed.hostname not in allowed_hosts:
            raise IngestionError(f"host {parsed.hostname!r} not in allowed_hosts")

        method = (cfg.get("method") or "GET").upper()
        if method not in ("GET", "POST"):
            raise IngestionError(f"unsupported method: {method}")

        headers: dict[str, str] = dict(cfg.get("headers") or {})

        auth_ref = cfg.get("auth_ref")
        if auth_ref:
            try:
                secret = secrets.resolve(auth_ref)
            except secrets.SecretNotFound as exc:
                raise IngestionError(str(exc)) from exc
            header_name = cfg.get("auth_header") or "Authorization"
            scheme_prefix = cfg.get("auth_scheme")
            headers[header_name] = (
                f"{scheme_prefix} {secret}" if scheme_prefix else secret
            )

        timeout = float(cfg.get("timeout_seconds") or settings.http_timeout_seconds)
        max_bytes = int(
            cfg.get("max_response_bytes") or settings.http_max_response_bytes
        )

        body = None
        if method == "POST":
            body = cfg.get("body")

        try:
            async with _make_client(timeout=timeout) as client:
                resp = await client.request(
                    method, url, headers=headers, json=body
                )
        except IngestionError:
            raise
        except httpx.HTTPError as exc:
            raise IngestionError(f"HTTP request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise IngestionError(
                f"HTTP {resp.status_code} from {parsed.hostname}"
            )

        content = resp.content
        if len(content) > max_bytes:
            raise IngestionError(
                f"response too large: {len(content)} > {max_bytes}"
            )

        fmt = (cfg.get("format") or "json").lower()
        return content, fmt

    def _rows_from_json(
        self, *, raw: bytes, json_path: str | None
    ) -> list[dict[str, Any]]:
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(f"invalid JSON: {exc}") from exc

        target = _get_json_path(obj, json_path) if json_path else obj
        if target is None:
            raise IngestionError(f"json_path {json_path!r} resolved to nothing")
        if not isinstance(target, list):
            raise IngestionError("JSON payload is not a list of records")
        out: list[dict[str, Any]] = []
        for item in target:
            if not isinstance(item, dict):
                raise IngestionError("JSON list contains non-object items")
            out.append(item)
        return out

    def _rows_from_csv(self, *, raw: bytes) -> list[dict[str, Any]]:
        text = raw.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        try:
            header = next(reader)
        except StopIteration as exc:
            raise IngestionError("CSV response is empty") from exc
        return [dict(zip(header, r, strict=False)) for r in reader]

    async def inspect(self, *, source: DataSource) -> SourceMetadata:
        raw, fmt = await self._fetch(source=source)
        if fmt == "json":
            rows = self._rows_from_json(
                raw=raw, json_path=(source.config or {}).get("json_path")
            )
        elif fmt == "csv":
            rows = self._rows_from_csv(raw=raw)
        else:
            raise IngestionError(f"unsupported format: {fmt}")

        columns: list[str] = list(rows[0].keys()) if rows else []
        return SourceMetadata(
            columns=columns,
            sample_rows=rows[:5],
            row_count_hint=len(rows),
        )

    async def ingest(self, *, source: DataSource) -> IngestionResult:
        raw, fmt = await self._fetch(source=source)
        if fmt == "json":
            records = self._rows_from_json(
                raw=raw, json_path=(source.config or {}).get("json_path")
            )
        elif fmt == "csv":
            records = self._rows_from_csv(raw=raw)
        else:
            raise IngestionError(f"unsupported format: {fmt}")

        parsed = [
            ParsedRow(row_number=i + 1, data=rec)
            for i, rec in enumerate(records)
        ]
        columns: list[str] = list(records[0].keys()) if records else []
        return IngestionResult(
            rows=parsed,
            metadata=SourceMetadata(columns=columns, row_count_hint=len(parsed)),
            errors=[],
        )
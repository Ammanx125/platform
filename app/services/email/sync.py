# app/services/email/sync.py
"""
Email ingestion.

One call to sync_email_account() pulls new messages since the account's
stored cursor, classifies each one, creates one Document + one StagedRow
per message, and advances the cursor.

Called from app/services/ingestion/service.py in the email_gmail /
email_graph branch of run_job(). It is not a PullConnector because email
produces Documents (like PDF), not tabular rows.

Provider selection happens here, not in the caller: the source of truth is
EmailAccount.provider. This lets tests use provider='fake' against a
DataSource whose source_type is still 'email_gmail' — no special-casing
anywhere upstream.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import DataSource, IngestionJob, IngestionLineage
from app.db.models.email import EmailAccount
from app.db.models.knowledge import Document
from app.services.email.base import (
    EmailProviderError,
    FetchedEmail,
)
from app.services.email.classifier import get_classifier
from app.services.email.provider_registry import get_provider
from app.services.email.tokens import (
    deserialize_tokens,
    is_expired,
    serialize_tokens,
)
from app.services.ingestion.base import IngestionError
from app.services.knowledge import service as knowledge_service

DEFAULT_MAX_MESSAGES_PER_JOB = 50
# Body chars per Document. Full body is preserved in raw_text; this only
# bounds the per-message size we hand to the chunker in one go.
_MAX_BODY_CHARS = 200_000


async def sync_email_account(
    db: AsyncSession,
    *,
    source: DataSource,
    job: IngestionJob,
    lineage_row: IngestionLineage,
    max_messages: int = DEFAULT_MAX_MESSAGES_PER_JOB,
) -> tuple[int, int]:
    """
    Run one sync cycle for one email account.

    Returns (messages_seen, documents_created). Callers set these on the
    job: rows_read = messages_seen, rows_staged = documents_created.

    Raises IngestionError on provider/auth problems; the caller's
    run_job() catch-block marks the job failed.
    """
    account = (
        await db.execute(
            select(EmailAccount).where(
                EmailAccount.source_id == source.id,
                EmailAccount.tenant_id == source.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise IngestionError(
            f"email source {source.id} has no EmailAccount row"
        )
    if account.status == "revoked":
        raise IngestionError("email account has been revoked")

    provider = get_provider(account.provider)

    tokens = deserialize_tokens(account.oauth_tokens_encrypted)
    if is_expired(tokens):
        try:
            tokens = await provider.refresh_tokens(tokens=tokens)
        except EmailProviderError as exc:
            account.status = "error"
            account.last_error = f"token refresh failed: {exc}"
            await db.flush()
            raise IngestionError(f"token refresh failed: {exc}") from exc
        account.oauth_tokens_encrypted = serialize_tokens(tokens)
        await db.flush()

    try:
        message_ids, next_cursor = await provider.list_message_ids(
            tokens=tokens,
            cursor=account.sync_cursor,
            max_messages=max_messages,
        )
    except EmailProviderError as exc:
        account.status = "error"
        account.last_error = f"list_message_ids failed: {exc}"
        await db.flush()
        raise IngestionError(f"list_message_ids failed: {exc}") from exc

    classifier = get_classifier()
    created = 0
    for message_id in message_ids:
        try:
            fetched = await provider.get_message(
                tokens=tokens, message_id=message_id
            )
        except EmailProviderError as exc:
            # One bad message must not sink the whole sync.
            lineage_row.errors = list(lineage_row.errors or []) + [{
                "message_id": message_id,
                "error": f"get_message failed: {exc}",
            }]
            continue

        # Idempotency: skip if a Document for this message_id already
        # exists in this source. Prevents duplicate ingestion when cursors
        # overlap (retries, first-sync re-runs, etc.). Long-term this
        # becomes a partial unique index on doc_metadata->>'message_id';
        # for now the check is explicit and cheap because syncs are small.
        existing = (
            await db.execute(
                select(Document.id).where(
                    Document.tenant_id == source.tenant_id,
                    Document.source_id == source.id,
                    Document.doc_metadata["message_id"].astext == message_id,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue

        classification = classifier.classify(
            subject=fetched.subject,
            body_text=fetched.body_text,
            from_address=fetched.from_address,
            to_addresses=fetched.to_addresses,
        )

        await _materialize_message(
            db,
            source=source,
            job=job,
            fetched=fetched,
            classification=classification,
        )
        created += 1

    # Advance cursor and sync metadata. Even a partial sync advances the
    # cursor: we've handled what we can, and re-running would duplicate
    # work on the messages that already succeeded.
    if next_cursor is not None:
        account.sync_cursor = next_cursor
        account.sync_cursor_updated_at = _now()
    account.last_sync_at = _now()
    account.status = "active"
    account.last_error = None

    lineage_row.source_uri = f"{account.provider}:{account.email_address}"
    lineage_row.source_cursor = account.sync_cursor

    await db.flush()
    return len(message_ids), created


async def _materialize_message(
    db: AsyncSession,
    *,
    source: DataSource,
    job: IngestionJob,
    fetched: FetchedEmail,
    classification,  # EmailClassification; untyped to avoid circular import
) -> None:
    """
    Create one Document + one StagedRow for one email.

    The Document is the retrieval-side representation (subject + body).
    The StagedRow is the structured-side representation (fields, intent,
    urgency) so future semantic mappings can join email to canonical
    concepts without touching the Document path.

    Both rows carry the same message_id so they can be correlated.
    """
    from app.db.models.dataset import StagedRow

    body = fetched.body_text or ""
    if len(body) > _MAX_BODY_CHARS:
        body = body[:_MAX_BODY_CHARS]

    doc_metadata = {
        "message_id": fetched.message_id,
        "thread_id": fetched.thread_id,
        "from_address": fetched.from_address,
        "from_name": fetched.from_name,
        "to_addresses": fetched.to_addresses,
        "cc_addresses": fetched.cc_addresses,
        "subject": fetched.subject,
        "received_at": fetched.received_at.isoformat(),
        "labels": fetched.labels,
        "intent": classification.intent,
        "urgency": classification.urgency,
        "classification_confidence": classification.confidence,
        "classification_method": classification.method,
        "classification_matched": classification.matched,
    }

    title = fetched.subject or f"(no subject) - {fetched.from_address}"
    if len(title) > 500:
        title = title[:497] + "..."

    raw_text = f"Subject: {fetched.subject}\n\n{body}"

    await knowledge_service.create_document(
        db,
        tenant_id=source.tenant_id,
        title=title,
        content_type="email",
        raw_text=raw_text,
        source_id=source.id,
        doc_metadata=doc_metadata,
    )

    row_data = {
        "message_id": fetched.message_id,
        "thread_id": fetched.thread_id,
        "from_address": fetched.from_address,
        "from_name": fetched.from_name,
        "to_addresses": fetched.to_addresses,
        "cc_addresses": fetched.cc_addresses,
        "subject": fetched.subject,
        "received_at": fetched.received_at.isoformat(),
        "snippet": fetched.snippet,
        "intent": classification.intent,
        "urgency": classification.urgency,
        "classification_confidence": classification.confidence,
        "classification_method": classification.method,
    }
    # Row number is per-job. We use the count of StagedRows already in this
    # job; the unique (job_id, row_number) constraint guards correctness.
    next_row_number = (
        await db.execute(
            select(StagedRow.row_number)
            .where(StagedRow.job_id == job.id)
            .order_by(StagedRow.row_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    row_number = (next_row_number or 0) + 1

    db.add(StagedRow(
        tenant_id=source.tenant_id,
        job_id=job.id,
        source_id=source.id,
        row_number=row_number,
        raw_data=row_data,
        search_text=json.dumps(row_data, ensure_ascii=False, sort_keys=True, default=str),
    ))
    await db.flush()


def _now():
    from datetime import UTC, datetime
    return datetime.now(UTC)
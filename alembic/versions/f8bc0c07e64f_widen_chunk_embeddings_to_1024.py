"""widen chunk embeddings to 1024

Revision ID: f8bc0c07e64f
Revises: 2593284c8725
Create Date: 2026-09-21 17:08:08.753229

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = 'f8bc0c07e64f'
down_revision: str | None = '2593284c8725'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the HNSW index before altering the column — pgvector cannot
    # rebuild the index across a dimension change while it exists.
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")

    # Existing rows (if any) cannot be converted; they were produced by the
    # mock provider at 384 dims. Truncate them — a re-embed will repopulate.
    # If you had real 384-dim data you wanted to keep, this would need a
    # two-phase migration: create a new column, backfill, swap.
    op.execute("DELETE FROM chunks")

    op.execute(
        "ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1024)"
    )

    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("DELETE FROM chunks")
    op.execute(
        "ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(384)"
    )
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
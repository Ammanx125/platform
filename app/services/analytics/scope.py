# class SnapshotScopeError(Exception): ...

# async def active_snapshot_filter(
#     db, *, tenant_id, source_ids: list[uuid.UUID]
# ) -> list[ColumnElement[bool]]:
#     """
#     Return SQLAlchemy WHERE clauses for a StagedRow query.

#     - All sources append-mode → return [].
#     - All sources replace_active → return [StagedRow.job_id == <active job per source>].
#       (Implemented as an OR of per-source clauses when there are multiple
#        replace_active sources.)
#     - Mixed → raise SnapshotScopeError.
#     """
# app/services/audit/types.py
"""
Audit event type constants.

An AuditEvent represents a meaningful business/system state transition
that an authorized human should be able to audit. It is NOT a debug log
and NOT an operational event stream.

Distinction from Event (app/services/events/types.py):

  Event      — "What happened that the system may need to react to?"
               Operational. Feeds workflow triggers.

  AuditEvent — "What meaningful action/state transition should an
               authorized human be able to audit?"
               Administrative. Feeds the audit view.

Constants are strings, matching the pattern in events/types.py. There is
no registration mechanism; the set of auditable events is small and
controlled.
"""
from __future__ import annotations

# --- Actions (emitted by app/services/actions/service.py) ---
ACTION_PENDING_APPROVAL = "action.pending_approval"
ACTION_APPROVED = "action.approved"
ACTION_REJECTED = "action.rejected"
ACTION_EXECUTED = "action.executed"
ACTION_FAILED = "action.failed"
ACTION_VERIFICATION_FAILED = "action.verification_failed"

# --- Decisions (emitted by app/services/orchestration/executor.py) ---
DECISION_CREATED = "decision.created"

# --- Workflows (emitted by app/services/workflows/engine.py) ---
WORKFLOW_COMPLETED = "workflow.completed"
WORKFLOW_FAILED = "workflow.failed"
WORKFLOW_REJECTED = "workflow.rejected"

# --- Ingestion (emitted by app/services/ingestion/service.py) ---
INGESTION_COMPLETED = "ingestion.completed"
INGESTION_FAILED = "ingestion.failed"


ALL_AUDIT_EVENT_TYPES: frozenset[str] = frozenset({
    ACTION_PENDING_APPROVAL,
    ACTION_APPROVED,
    ACTION_REJECTED,
    ACTION_EXECUTED,
    ACTION_FAILED,
    ACTION_VERIFICATION_FAILED,
    DECISION_CREATED,
    WORKFLOW_COMPLETED,
    WORKFLOW_FAILED,
    WORKFLOW_REJECTED,
    INGESTION_COMPLETED,
    INGESTION_FAILED,
})
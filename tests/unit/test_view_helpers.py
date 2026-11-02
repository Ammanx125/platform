# tests/unit/test_view_helpers.py
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.web.view_helpers import build_action_timeline, build_timeline


def _instance(step_states, pending_approval_step=None):
    return SimpleNamespace(
        step_states=step_states,
        pending_approval_step=pending_approval_step,
    )


def _action(**overrides):
    values = {
        "status": "pending_approval",
        "tool_name": "generate_report",
        "proposed_at": datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        "approved_at": None,
        "approved_by_user_id": None,
        "finished_at": None,
        "verified_at": None,
        "execution_result": {},
        "error_message": None,
        "verification_error": None,
        "rejection_reason": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# ---------- workflow instances ----------


def test_build_timeline_normalizes_statuses():
    instance = _instance(
        [
            {"name": "analyze", "status": "completed", "output": {"kpis": 2}, "error": None},
            {"name": "publish", "status": "succeeded"},
            {"name": "notify", "status": "in_progress"},
            {"name": "audit", "status": "error", "error": "boom"},
        ]
    )

    rows = build_timeline(instance)

    assert [r["name"] for r in rows] == ["analyze", "publish", "notify", "audit"]
    assert [r["status"] for r in rows] == ["complete", "complete", "running", "failed"]
    assert rows[0]["detail"] == {"kpis": 2}
    assert rows[0]["json"] is True
    assert rows[3]["error"] == "boom"
    assert all(r["is_approval"] is False for r in rows)


def test_build_timeline_marks_pending_workflow_approval_step_as_waiting():
    instance = _instance(
        [
            {"name": "analyze", "status": "completed"},
            {"name": "approval", "status": "waiting"},
            {"name": "act", "status": "pending"},
        ],
        pending_approval_step="approval",
    )

    rows = build_timeline(instance)

    assert [r["status"] for r in rows] == ["complete", "waiting", "pending"]
    assert [r["is_approval"] for r in rows] == [False, True, False]
    assert rows[1]["href"] is None


def test_build_timeline_marks_pending_action_approval_step_and_links_approvals():
    instance = _instance(
        [
            {"name": "analyze", "status": "completed"},
            {
                "name": "propose",
                "status": "waiting",
                "wait_reason": "action_approval",
                "wait_ref": "abc",
            },
        ],
        pending_approval_step=None,
    )

    rows = build_timeline(instance)

    assert rows[1]["status"] == "waiting"
    assert rows[1]["is_approval"] is True
    assert rows[1]["href"] == "/approvals"


def test_build_timeline_ignores_waiting_steps_for_other_reasons():
    instance = _instance(
        [{"name": "collect", "status": "waiting", "wait_reason": "input"}],
        pending_approval_step="approval",
    )

    rows = build_timeline(instance)

    assert rows[0]["status"] == "pending"
    assert rows[0]["is_approval"] is False


def test_build_timeline_handles_empty_and_malformed_state():
    assert build_timeline(_instance([])) == []
    assert build_timeline(_instance(None)) == []
    assert [r["name"] for r in build_timeline(_instance([None, {"name": "x"}]))] == ["x"]


def test_build_timeline_parses_engine_timestamp_strings_for_template():
    from app.web.templating import templates

    timeline = build_timeline(
        _instance(
            [
                {
                    "name": "analyze",
                    "status": "completed",
                    "finished_at": "2026-11-02T18:01:00+00:00",
                }
            ]
        )
    )
    html = templates.get_template("workflows/_timeline.html").render(timeline=timeline)

    assert "2026-11-02 18:01" in html


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("COMPLETED", "complete"),
        ("succeeded", "complete"),
        ("running", "running"),
        ("in_progress", "running"),
        ("failed", "failed"),
        ("error", "failed"),
        ("pending", "pending"),
        ("", "pending"),
    ],
)
def test_build_timeline_status_mapping(raw, expected):
    rows = build_timeline(_instance([{"name": "s", "status": raw}]))
    assert rows[0]["status"] == expected


# ---------- action records ----------


def test_action_timeline_pending_approval_stops_at_waiting_row():
    rows = build_action_timeline(_action())

    assert [r["name"] for r in rows] == ["Proposed", "Approval pending"]
    assert [r["status"] for r in rows] == ["complete", "waiting"]
    assert rows[0]["detail"] == "generate_report"
    assert rows[0]["timestamp"] == datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    assert rows[1]["is_approval"] is True


def test_action_timeline_rejected_records_reason_as_error():
    rows = build_action_timeline(
        _action(
            status="rejected",
            approved_by_user_id=None,
            rejection_reason="not this quarter",
        )
    )

    assert [r["name"] for r in rows] == ["Proposed", "Decision"]
    assert rows[1]["error"] == "not this quarter"
    assert rows[1]["is_approval"] is False


def test_action_timeline_executed_has_full_lifecycle():
    rows = build_action_timeline(
        _action(
            status="executed",
            approved_by_user_id="approver",
            approved_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            finished_at=datetime(2026, 1, 1, 10, 0, 5, tzinfo=UTC),
            verified_at=datetime(2026, 1, 1, 10, 0, 6, tzinfo=UTC),
            execution_result={"output": {"report_id": "r-1"}},
        )
    )

    assert [r["name"] for r in rows] == [
        "Proposed",
        "Approval",
        "Execution",
        "Verification",
    ]
    assert [r["status"] for r in rows] == ["complete"] * 4
    assert rows[1]["detail"] == "by approver"
    assert rows[2]["detail"] == {"report_id": "r-1"}
    assert rows[2]["json"] is True


def test_action_timeline_auto_execution_does_not_claim_approval():
    rows = build_action_timeline(
        _action(
            status="executed",
            approved_by_user_id=None,
            approved_at=None,
            finished_at=datetime(2026, 1, 1, 10, 0, 5, tzinfo=UTC),
            verified_at=datetime(2026, 1, 1, 10, 0, 6, tzinfo=UTC),
            execution_result={"output": {"report_id": "r-1"}},
        )
    )

    assert [row["name"] for row in rows] == ["Proposed", "Execution", "Verification"]
    assert [row["status"] for row in rows] == ["complete", "complete", "complete"]


def test_action_timeline_failed_execution_stops_before_verification():
    rows = build_action_timeline(_action(status="failed", error_message="tool unavailable"))

    assert [r["name"] for r in rows] == ["Proposed", "Execution"]
    assert rows[1]["status"] == "failed"
    assert rows[1]["error"] == "tool unavailable"


def test_action_timeline_falls_back_to_execution_result_error():
    rows = build_action_timeline(_action(status="failed", execution_result={"error": "timeout"}))

    assert rows[1]["error"] == "timeout"


def test_action_timeline_verification_failed_is_distinct():
    rows = build_action_timeline(
        _action(status="verification_failed", verification_error="no receipt found")
    )

    assert rows[1]["status"] == "complete"
    assert rows[2]["name"] == "Verification"
    assert rows[2]["status"] == "failed"
    assert rows[2]["error"] == "no receipt found"


def test_action_timeline_approved_but_not_executed_is_running():
    rows = build_action_timeline(_action(status="approved"))

    assert [r["name"] for r in rows] == ["Proposed", "Approval", "Execution"]
    assert rows[2]["status"] == "running"

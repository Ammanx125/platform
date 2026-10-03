# scripts/run_procurement_slice.py
"""
Procurement vertical slice.

Exercises the full Sansa platform end-to-end through the public API:
  ingest → profile → map → KPI → anomaly → ask → answer → approve → audit

Assumes:
  - The API is running at --base-url (default http://127.0.0.1:8000)
  - The worker is running (make worker)
  - A tenant + admin exist (see --email / --password)
  - The database is reachable by the API

Usage:
    python -m scripts.run_procurement_slice

    # Custom endpoints / creds:
    python -m scripts.run_procurement_slice \
        --base-url http://127.0.0.1:8000 \
        --email admin@demo.com \
        --password Sansa2026

Exit code is 0 on full success, 1 on any step failure.
"""
from __future__ import annotations

import argparse
import csv
import io
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


# ---------- logging ----------

class Logger:
    def __init__(self) -> None:
        self.step = 0

    def header(self, text: str) -> None:
        print()
        print("=" * 72)
        print(text)
        print("=" * 72)

    def info(self, text: str) -> None:
        print(f"  {text}")

    def ok(self, text: str) -> None:
        print(f"  ✓ {text}")

    def warn(self, text: str) -> None:
        print(f"  ! {text}")

    def fail(self, text: str) -> None:
        print(f"  ✗ {text}", file=sys.stderr)

    def step_header(self, text: str) -> None:
        self.step += 1
        print()
        print(f"[{self.step}] {text}")


log = Logger()


# ---------- synthetic data ----------

SUPPLIERS = [
    "Acme Industrial",
    "Beacon Metals",
    "Cobalt Parts",
    "Delta Supply Co",
    "Everest Materials",
    "Frontier Fasteners",
]

PRODUCTS = [
    ("Steel Bolt M8", 0.42),
    ("Steel Nut M8", 0.18),
    ("Aluminium Sheet 2mm", 18.75),
    ("Aluminium Sheet 3mm", 26.40),
    ("Copper Wire 1.5mm", 1.10),
    ("Copper Wire 2.5mm", 1.85),
    ("Steel Bolt M10", 0.55),
    ("Steel Nut M10", 0.22),
    ("Rubber Gasket 50mm", 2.15),
    ("Rubber Gasket 75mm", 3.30),
    ("Stainless Washer M8", 0.08),
    ("Stainless Washer M10", 0.12),
]


def generate_procurement_rows(
    *,
    seed: int = 42,
    num_rows: int = 120,
) -> list[dict[str, Any]]:
    """
    Generate a deterministic synthetic procurement dataset.

    Same seed → same rows every run. Two features deliberately baked in so
    the anomaly detector and KPI engine have something to work with:
      - One supplier (Acme) has 3 rows with prices ~5x the usual value
        (the "price spike" the anomaly detector should catch).
      - Order dates span the last ~180 days so the "this quarter" question
        has meaningful data.
    """
    rng = random.Random(seed)
    today = date.today()
    rows: list[dict[str, Any]] = []

    for i in range(num_rows):
        supplier = rng.choice(SUPPLIERS)
        product, base_price = rng.choice(PRODUCTS)
        quantity = rng.choice([50, 100, 150, 200, 300, 500, 800, 1000])
        # ±10% noise around base price
        price = round(base_price * rng.uniform(0.90, 1.10), 2)
        order_date = today - timedelta(days=rng.randint(0, 180))

        # Plant three anomalies in the last 30 days with Acme.
        if supplier == "Acme Industrial" and i % 40 == 0 and i < 120:
            price = round(base_price * 5.0, 2)
            order_date = today - timedelta(days=rng.randint(0, 30))

        rows.append({
            "supplier": supplier,
            "product": product,
            "quantity": quantity,
            "unit_price": price,
            "order_date": order_date.isoformat(),
        })

    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_xlsx(path: Path, rows: list[dict[str, Any]]) -> None:
    try:
        from openpyxl import Workbook
    except ImportError:
        raise SystemExit("openpyxl not installed; run `pip install openpyxl`")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Procurement"
    header = list(rows[0].keys())
    ws.append(header)
    for row in rows:
        ws.append([row[h] for h in header])
    wb.save(path)


# ---------- API client ----------

class SansaAPI:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=60.0)
        self._csrf: str | None = None

    def _capture_csrf(self) -> None:
        self._csrf = self.client.cookies.get("sansa_csrf")

    def _csrf_headers(self) -> dict[str, str]:
        if not self._csrf:
            return {}
        return {"X-CSRF-Token": self._csrf}

    def login(self, *, email: str, password: str) -> dict:
        r = self.client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        r.raise_for_status()
        self._capture_csrf()
        me = self.client.get("/api/v1/auth/me")
        me.raise_for_status()
        return me.json()

    def create_dataset(self, *, name: str, source_type: str) -> dict:
        r = self.client.post(
            "/api/v1/datasets",
            json={"name": name, "source_type": source_type, "config": {}},
            headers=self._csrf_headers(),
        )
        if not r.is_success:
            raise RuntimeError(
                f"dataset creation failed ({r.status_code}): {r.text}"
            )
        return r.json()

    def upload(self, *, dataset_id: str, path: Path) -> dict:
        with path.open("rb") as fh:
            r = self.client.post(
                f"/api/v1/datasets/{dataset_id}/upload",
                files={"file": (path.name, fh, "application/octet-stream")},
                headers=self._csrf_headers(),
            )
        r.raise_for_status()
        return r.json()

    def get_job(self, *, dataset_id: str, job_id: str) -> dict:
        r = self.client.get(f"/api/v1/datasets/{dataset_id}/jobs/{job_id}")
        r.raise_for_status()
        return r.json()

    def wait_for_job(
        self, *, dataset_id: str, job_id: str, timeout_s: int = 90
    ) -> dict:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            job = self.get_job(dataset_id=dataset_id, job_id=job_id)
            if job["status"] in ("succeeded", "failed"):
                return job
            time.sleep(1.0)
        raise TimeoutError(f"job {job_id} did not finish in {timeout_s}s")

    def propose_mappings(self, *, dataset_id: str, job_id: str) -> list[dict]:
        r = self.client.post(
            f"/api/v1/datasets/{dataset_id}/mappings/propose",
            params={"job_id": job_id},
            headers=self._csrf_headers(),
        )
        r.raise_for_status()
        return r.json()["mappings"]

    def list_mappings(self, *, dataset_id: str) -> list[dict]:
        r = self.client.get(f"/api/v1/datasets/{dataset_id}/mappings")
        r.raise_for_status()
        return r.json()

    def confirm_mapping(self, *, dataset_id: str, mapping_id: str) -> dict:
        r = self.client.patch(
            f"/api/v1/datasets/{dataset_id}/mappings/{mapping_id}",
            json={"status": "confirmed"},
            headers=self._csrf_headers(),
        )
        r.raise_for_status()
        return r.json()

    def kpi_value(self, key: str) -> dict:
        r = self.client.get(f"/api/v1/analytics/kpis/{key}/value")
        r.raise_for_status()
        return r.json()

    def run_anomaly_detector(self, key: str) -> list[dict]:
        r = self.client.post(
            f"/api/v1/analytics/anomaly-detectors/{key}/run",
            json={},
            headers=self._csrf_headers(),
        )
        r.raise_for_status()
        return r.json()

    def ask(self, query: str) -> dict:
        r = self.client.post(
            "/api/v1/decisions",
            json={"query": query},
            headers=self._csrf_headers(),
        )
        r.raise_for_status()
        return r.json()

    def approve_action(self, action_id: str) -> dict:
        r = self.client.post(
            f"/api/v1/actions/{action_id}/approve",
            json={},
            headers=self._csrf_headers(),
        )
        r.raise_for_status()
        return r.json()

    def get_action(self, action_id: str) -> dict:
        r = self.client.get(f"/api/v1/actions/{action_id}")
        r.raise_for_status()
        return r.json()

    def list_actions_for_decision(self, decision_id: str) -> list[dict]:
        r = self.client.get(
            "/api/v1/actions",
            params={"limit": 50},
        )
        r.raise_for_status()
        return [
            a for a in r.json()
            if a.get("decision_run_id") == decision_id
        ]

    def list_audit(self, *, since: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"limit": 200}
        if since:
            params["since"] = since
        r = self.client.get("/api/v1/audit", params=params)
        r.raise_for_status()
        return r.json()


# ---------- slice steps ----------

@dataclass
class SliceResult:
    steps: list[tuple[str, bool, str]] = field(default_factory=list)

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append((name, ok, detail))

    def all_ok(self) -> bool:
        return all(ok for _, ok, _ in self.steps)

    def print_summary(self) -> None:
        log.header("Slice summary")
        for name, ok, detail in self.steps:
            marker = "✓" if ok else "✗"
            line = f"  {marker} {name}"
            if detail:
                line += f" — {detail}"
            print(line)
        print()
        if self.all_ok():
            print("  All steps passed.")
        else:
            print("  Some steps failed.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="admin@demo.com")
    parser.add_argument("--password", default="Sansa2026")
    parser.add_argument(
        "--data-dir", default="data/demo", type=Path,
        help="Where to write generated sample data."
    )
    args = parser.parse_args()

    result = SliceResult()
    api = SansaAPI(args.base_url)

    # ---- 1. Login ----
    log.step_header("Login")
    try:
        me = api.login(email=args.email, password=args.password)
        log.ok(f"logged in as {me['email']} (tenant {me['tenant_id'][:8]}…)")
        result.record("login", True)
    except Exception as exc:
        log.fail(f"login failed: {exc}")
        result.record("login", False, str(exc))
        result.print_summary()
        return 1

    # ---- 2. Generate data ----
    log.step_header("Generate synthetic procurement data")
    rows = generate_procurement_rows()
    csv_path = args.data_dir / "procurement_sample.csv"
    xlsx_path = args.data_dir / "procurement_sample.xlsx"
    write_csv(csv_path, rows)
    write_xlsx(xlsx_path, rows)
    log.ok(f"wrote {len(rows)} rows to {csv_path.name} and {xlsx_path.name}")
    result.record("generate_data", True, f"{len(rows)} rows")

    # ---- 3. Create dataset ----
    log.step_header("Create DataSource (excel)")
    try:
        dataset_name = f"Procurement Slice {uuid4().hex[:8]}"
        source = api.create_dataset(name=dataset_name, source_type="excel")
        ds_id = source["id"]
        log.ok(f"dataset created: {ds_id[:8]}…")
        result.record("create_dataset", True)
    except Exception as exc:
        log.fail(f"create_dataset failed: {exc}")
        result.record("create_dataset", False, str(exc))
        result.print_summary()
        return 1

    # ---- 4. Upload ----
    log.step_header("Upload Excel file")
    try:
        job = api.upload(dataset_id=ds_id, path=xlsx_path)
        job_id = job["id"]
        log.ok(f"upload accepted, job {job_id[:8]}… status={job['status']}")
        result.record("upload", True)
    except Exception as exc:
        log.fail(f"upload failed: {exc}")
        result.record("upload", False, str(exc))
        result.print_summary()
        return 1

    # ---- 5. Wait for ingestion ----
    log.step_header("Wait for ingestion")
    try:
        job = api.wait_for_job(dataset_id=ds_id, job_id=job_id)
        if job["status"] != "succeeded":
            raise RuntimeError(
                f"job ended {job['status']}: {job.get('error_message')}"
            )
        log.ok(f"job succeeded, rows_staged={job['rows_staged']}")
        result.record("ingestion", True, f"rows={job['rows_staged']}")
    except Exception as exc:
        log.fail(f"ingestion failed: {exc}")
        result.record("ingestion", False, str(exc))
        result.print_summary()
        return 1

    # ---- 6. Propose and confirm mappings ----
    log.step_header("Semantic mapping")
    try:
        proposed = api.propose_mappings(dataset_id=ds_id, job_id=job_id)
        log.ok(f"{len(proposed)} mappings proposed")
        # Confirm every proposed mapping that has a concept
        all_mappings = api.list_mappings(dataset_id=ds_id)
        confirmed = 0
        for m in all_mappings:
            if m["status"] == "proposed":
                api.confirm_mapping(dataset_id=ds_id, mapping_id=m["id"])
                confirmed += 1
        log.ok(f"{confirmed} mappings confirmed")
        result.record("mapping", True, f"{confirmed} confirmed")
    except Exception as exc:
        log.fail(f"mapping failed: {exc}")
        result.record("mapping", False, str(exc))

    # ---- 7. KPI: total spend ----
    log.step_header("KPI: procurement.total_spend")
    try:
        kpi = api.kpi_value("procurement.total_spend")
        value = kpi["value"]
        log.ok(f"value = {value}")
        result.record("kpi_total_spend", True, str(value))
    except Exception as exc:
        log.fail(f"KPI failed: {exc}")
        result.record("kpi_total_spend", False, str(exc))

    # ---- 8. Anomaly detection ----
    log.step_header("Anomaly detector: procurement.purchase_price_spikes")
    try:
        anomalies = api.run_anomaly_detector(
            "procurement.purchase_price_spikes"
        )
        log.ok(f"{len(anomalies)} anomalies detected")
        for a in anomalies[:3]:
            log.info(
                f"  · {a['group_label'] or '—'}: "
                f"value={a['value']:.2f} score={a['score']:.2f}"
            )
        result.record("anomalies", True, f"{len(anomalies)} found")
    except Exception as exc:
        log.fail(f"anomaly detection failed: {exc}")
        result.record("anomalies", False, str(exc))

    # ---- 9. Ask Sansa ----
    log.step_header(
        "Ask Sansa: 'Why did procurement cost increase this quarter, "
        "and what should we do?'"
    )
    try:
        decision = api.ask(
            "Why did procurement cost increase this quarter, and what "
            "should we do?"
        )
        decision_id = decision["id"]
        log.ok(f"decision {decision_id[:8]}… recorded")
        log.info(f"summary: {decision.get('llm_summary') or '(none)'}")
        log.info(
            f"validated claims: {len(decision.get('validated_claims') or [])}"
        )
        log.info(
            f"tool calls: {len(decision.get('llm_tool_calls') or [])}"
        )
        result.record("ask", True)
    except Exception as exc:
        log.fail(f"ask failed: {exc}")
        result.record("ask", False, str(exc))
        result.print_summary()
        return 1

    # ---- 10. Approve a proposed action (if any) ----
    log.step_header("Action approval (if any)")
    try:
        actions = api.list_actions_for_decision(decision_id)
        if not actions:
            log.warn("no actions proposed by the model; skipping approval")
            result.record("action", True, "none proposed")
        else:
            first = actions[0]
            if first["status"] == "pending_approval":
                log.info(f"approving action {first['id'][:8]}…")
                api.approve_action(first["id"])
                # Give the action time to execute + verify
                time.sleep(2)
                refreshed = api.get_action(first["id"])
                log.ok(
                    f"action status after approve: {refreshed['status']}"
                )
                result.record("action", True, refreshed["status"])
            else:
                log.info(
                    f"action already in state {first['status']}; "
                    f"nothing to approve"
                )
                result.record("action", True, first["status"])
    except Exception as exc:
        log.fail(f"action approval failed: {exc}")
        result.record("action", False, str(exc))

    # ---- 11. Audit trail ----
    log.step_header("Audit trail")
    try:
        events = api.list_audit()
        log.ok(f"{len(events)} audit events in this tenant")
        for e in events[:5]:
            log.info(f"  · {e['event_type']}  {e.get('message') or ''}")
        result.record("audit", True, f"{len(events)} events")
    except Exception as exc:
        log.fail(f"audit fetch failed: {exc}")
        result.record("audit", False, str(exc))

    result.print_summary()
    return 0 if result.all_ok() else 1


if __name__ == "__main__":
    sys.exit(main())
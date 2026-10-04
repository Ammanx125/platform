# Sansa presentation and rehearsal manual

## Before you rehearse: four important caveats

1. **The current `.env` uses a mock chat model.** I checked the loaded settings: `LLM_PROVIDER=mock`. The 2.24 GB download is the separate **embedding model** used to search relevant information; it is not the chat LLM. With the current setting, the chat response is literally **“Mock response.”** You can demonstrate the dashboard’s saved analytics and deterministic workflow steps, but don’t present the chat as real LLM reasoning until a real OpenAI-compatible model server is running and Sansa is configured to use it. The platform’s SGLang adapter points to `http://127.0.0.1:8001/v1`, but neither the platform repo nor the watcher repo provides a verified command to start that model server. See `.env` and `the SGLang adapter`.

2. **The seeded demo does not create a pending approval.** The approval buttons only appear when there is a pending action. The current mock LLM doesn’t propose one, so you may have nothing to simulate unless a pending action is created separately. Also, “Simulate execution” is browser-only and does not execute or record anything on the server; “Approve and execute” is a real server action.

The dashboard’s analytics and filesystem watcher can be demonstrated independently of the mock chat model. Don’t present the mock response as live LLM reasoning. The additional frontend improvements to consider are listed at the end.

---

## 1. Which tenant to use

Use the prebuilt demo tenant:

- **Tenant name:** Meridian Transport
- **Login workspace/tenant slug:** `meridian-transport-demo`
- **Admin email/password:** you choose these when seeding; there is no default demo password.
- **Access:** the seed gives the user the tenant’s admin role and creates a system user for system-triggered work.

There is **no public sign-up page**. The login form asks for workspace slug, email, and password. The demo seed provisions the tenant; it is safe to rerun. If the email already exists, the password must match its existing password—the seed will not reset it. If you don’t know the old password, use a **new email** to add another admin to the same demo tenant. The tenant is named and seeded by `seed_demo.py`; tenant/user properties are described in `tenant.py` and `user.py`.

For an unrelated, empty tenant, there is a CLI script called `create_admin.py`, but it does **not** add Meridian’s demo datasets. Use Meridian for your rehearsal.

---

## 2. Start the platform

Run each section in a separate PowerShell terminal where indicated. Don’t run `docker compose down -v`: that removes the database volume and can erase local demo data.

### Terminal 1 — database, migrations, embedding cache, and seed

```powershell
Set-Location C:\Users\LocalAdmin\Documents\platform

docker compose -f docker-compose.dev.yml up -d db
docker compose -f docker-compose.dev.yml ps

uv run alembic upgrade head
```

The Compose file starts PostgreSQL with pgvector on port **15432**. It does not start the API, worker, Redis, watcher, or model server. The platform’s app and API are the same FastAPI process; migrations are defined by Alembic.

**Preload the embedding model before your presentation.** This avoids having the first audience question trigger the download. The selected model is about 2.24 GB, and the persistent cache is `C:\Users\LocalAdmin\.cache\sansa\fastembed` unless `FASTEMBED_CACHE_DIR` overrides it.

```powershell
uv run python -c "from app.core.config import settings; from fastembed import TextEmbedding; TextEmbedding(model_name=settings.embedding_model, cache_dir=settings.fastembed_cache_dir); print('Embedding model cache is ready')"
```

Allow the first run to finish and confirm you have enough disk space. Don’t do this for the first time during the presentation. The setting and override are documented in `config.py` and `README.md`.

Now seed Meridian. Choose credentials you can enter at the login screen:

```powershell
$env:DEMO_ADMIN_EMAIL = "your-demo-admin@example.test"
$env:DEMO_ADMIN_PASSWORD = "<choose-a-strong-password>"

uv run python -m scripts.seed_demo

Remove-Item Env:DEMO_ADMIN_EMAIL, Env:DEMO_ADMIN_PASSWORD
```

Expected seed summary: **6 sources and 148 staged rows**. The seed generates synthetic CSV files and runs their ingestion and analytics. It can be rerun without resetting an existing user’s password or duplicating successful source jobs/forecasts.

### Terminal 2 — API and website

```powershell
Set-Location C:\Users\LocalAdmin\Documents\platform
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Check both endpoints:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/health/db
```

Open:

- Website: `http://127.0.0.1:8000`
- Interactive API docs: `http://127.0.0.1:8000/docs`

`/health` means the web/API process is responding. `/health/db` verifies its database connection. Neither checks the watcher or LLM server. Those endpoints are in `main.py`.

### Terminal 3 — background worker

```powershell
Set-Location C:\Users\LocalAdmin\Documents\platform
uv run python -m app.workers.main
```

Leave the terminal open. It processes ingestion, webhooks, pending/resuming workflows, and event triggers. An idle worker with no “processed item(s)” messages is normal. The task list and intervals are in `registry.py`; the worker loop is in `main.py`.

Development doesn’t require Redis: rate limiting is in-process. Production does require Redis, but the development Compose file does not start it.

### Terminal 4 — watcher demo through a signed webhook

The watcher’s usable demo mode posts signed webhook data. It is **not** the registered filesystem-agent mode and won’t appear as a registered Agent in Sansa.

1. Sign in to the platform in your browser.
2. Open `http://127.0.0.1:8000/docs`.
3. Use `POST /api/v1/datasets/webhook` to create a webhook source, with body:

   ```json
   {"name":"Meridian Live Watcher"}
   ```

4. Save the response’s `token` and `secret` privately. They are returned once. Don’t put them in a screenshot or presentation.
5. In a new terminal:

   ```powershell
   Set-Location C:\Users\LocalAdmin\Documents\sansa-agent

   $env:SANSA_DEMO_WEBHOOK_URL = "http://127.0.0.1:8000/api/v1/webhooks/<returned-token>"
   $env:SANSA_DEMO_WEBHOOK_SECRET = "<returned-secret>"

   .\.venv\Scripts\python.exe -m sansa_agent run-demo-watcher --interval 30
   ```

This mode periodically sends generated vehicle/fuel events. To send rows from the sample CSV instead, use the documented CSV variant:

```powershell
.\.venv\Scripts\python.exe -m sansa_agent run-demo-watcher `
  --mode csv `
  --source-file "C:\Users\LocalAdmin\Documents\platform\data\demo\procurement_sample.csv"
```

CSV mode sends unseen rows; it tracks state, so a second run may not resend unchanged rows. In either mode, keep the terminal open and show its logs. The platform worker turns received webhook deliveries into ingestion jobs; refresh **Data** to see the webhook source/jobs. This separate webhook source is not mapped into the six curated Meridian sources, so don’t claim its data changes Meridian’s seeded KPIs or forecasts. The watcher setup is described in the `sansa-agent README`.

---

## 3. Rehearsal: what to say and what to click

### A. Sign in and introduce the workspace

At `http://127.0.0.1:8000/login`, enter:

- **Workspace:** `meridian-transport-demo`
- **Email/password:** the values used for the seed

The header shows Sansa AI, the current workspace, a Data → Understanding → Decision → Action progress indicator, a dark-mode switch, your email, and **Sign out**. The progress indicator is a visual cue, not a live worker monitor.

### B. Overview — start here

Open **Overview** (`/`). It presents the latest saved analysis for the signed-in tenant:

- **Business snapshot cards:** the first three KPI definitions that produce values for this tenant, plus the pending-approval count. They are not hardcoded “revenue, margin, issues” cards; the actual KPIs depend on available mapped data.
- **Business outlook:** the latest saved successful forecast. It shows the metric, group if any, number/frequency of forecast steps, selected model, reliability, last generated time, and backtest error. The plotted line is **predicted values only**—not historical actuals, confidence bounds, or a guarantee.
- **Semantic understanding:** examples of original source columns mapped to confirmed canonical concepts. This is how differently named columns can feed common analytics without renaming the source data.
- **Customer health:** customer count, transaction count, recency/frequency-based segments, and attention profiles. Read the caveat above before presenting “declining” results. With only seven synthetic customers, “top 10 revenue share” is 100% by definition and isn’t a meaningful concentration demonstration.
- **Latest signals:** persisted anomaly records, not a live stream.
- **Management attention:** pending actions awaiting approval, if any; it may be empty in the seeded demo.
- **Latest decision:** a link to the most recent question’s saved record.
- **Ask Sansa:** enter a question and press **Ask Sansa**. A spinner says “Analyzing…”, and the answer appears beneath it. With the current mock configuration, the generated text will be “Mock response,” so fix the model configuration before presenting this as an LLM answer.

Suggested questions once the relevant services are verified:

- `How are operations performing?` — rules route this to the Operations SLA Monitoring workflow, whose steps evaluate KPIs and downtime anomalies.
- `Forecast operating costs for the next 30 days` — requests a forecast over mapped operating-cost history.
- Customer-risk questions should wait until the segmentation mismatch is fixed and validated.

The workflow’s deterministic routing and step definitions are in `planner.py` and `operations.py`.

### C. Data — show where the evidence comes from

Open **Data** (`/datasets`).

The list shows each source, source type, latest ingestion status, total and failed job counts, and creation date. Select a source name to open details:

- **Recent ingestion jobs:** status, staged row count, start time, duration, and any error.
- **Semantic mappings:** source column → canonical concept, mapping status, confidence.
- **Latest data profile:** row/column counts, inferred types, null rates, distinct counts, ranges, and any quality issues.

The Data pages are currently **read-only**; source creation and uploads happen through the API, not dashboard buttons. See `datasets/list.html` and `datasets/detail.html`.

### D. Decisions — inspect the saved answer and its support

Open **Decisions** (`/decisions`). Each row shows the question, model name, duration, and time. Select a question to inspect:

- conclusion and validated claims,
- IDs of the evidence supporting each claim,
- supporting KPI/anomaly/forecast/workflow/retrieved-content evidence,
- any recommended tool action and its status,
- unsupported claims and the reason they were dropped,
- the execution plan.

This is the best page to show how a question’s answer is tied to recorded evidence. See `decisions/list.html` and `decisions/detail.html`.

### E. Workflows — show the process behind an answer

Open **Workflows** (`/workflows`) to show registered domain workflows and their descriptions, step counts, and requirements. **View instances** opens that workflow’s run history. Select an instance to inspect its status, trigger, steps, outputs, errors, and context.

There is no “Run workflow” button on this page: workflows are reached through qualifying questions, such as the operations question above. See `workflows/list.html` and `workflows/instance_detail.html`.

### F. Approvals — distinguish simulation from execution

Open **Approvals** (`/approvals`). Expand a pending recommendation to read the rationale, evidence count/references, and technical details.

- **Simulate execution:** changes only the browser display and explicitly says no action ran or was recorded.
- **Approve and execute:** sends an approval to the server and attempts the real approved action.
- **Reject:** rejects the pending action on the server.

For a safe rehearsal, use **Simulate execution** only. Do not click **Approve and execute** unless you have confirmed what tool it will call and that it is safe. These cards are conditional; the demo seed doesn’t create a pending example. See `approvals/_row.html`.

### G. Audit — show the record of state changes

Open **Audit** (`/audit`). It is read-only. The event-type dropdown auto-submits when changed. Rows show timestamp, event type, subject, and message. Use this to show recorded transitions, not live watcher health.

### H. Header and navigation controls

- **Overview, Decisions, Approvals, Data, Workflows, Audit:** navigate to those pages.
- **Sansa AI wordmark:** returns to Overview.
- **Dark mode:** toggles the visual theme; it is stored in browser local storage.
- **Sign out:** ends the session and returns to login.
- **“Workspace view” green indicator:** currently a static label—not proof the watcher, worker, or LLM is online.

---

## 4. What makes the seeded analysis grounded

The seed creates six controlled CSV sources with **148 rows total**:

| Source | Rows | Story element |
|---|---:|---|
| Daily Operations | 12 | Weekly operating cost and capacity/downtime history |
| Ticket Transactions | 36 | Three routes with stable weekly passenger/revenue records |
| Fuel Records | 12 | Fuel-use rate ends with a deliberately large spike |
| Supplier Records | 12 | Supplier lead time ends with a deliberately large delay |
| Route Performance | 36 | Three routes with weekly distance/travel-time data |
| Customer Transactions | 40 | Seven synthetic customers with different purchase histories |

The revenue KPI is designed to total **209,310**: 192,000 from the ticket rows plus 17,310 from customer transactions. The integration test asserts that total, but the earlier DB-backed test run could not complete because PostgreSQL was unavailable; rerun it before relying on the database result.

The seeded anomaly signals are based on deliberately conspicuous values: operating downtime rises from roughly 1–1.5 hours to 18 hours; the fuel-use rate jumps to 11.8; supplier lead time jumps to 18. They are checked by configured anomaly detectors, not invented by the dashboard. The operating-cost series rises by 7.5% weekly, and the saved forecast uses the existing candidate/backtesting pipeline.

Synthetic source files are written under `data/uploads/<tenant-id>/<source-id>/...`; parsed rows, mappings, profiles, jobs, anomalies, forecasts, decisions, and audit events live in PostgreSQL. The sample watcher files are under `data/demo`. The model cache is separate from both, under `C:\Users\LocalAdmin\.cache\sansa\fastembed` by default.

---

## 5. What I would build or fix before the presentation

**Must resolve for an honest “live AI + watcher” demo:**

1. Configure and verify a real LLM endpoint; the current provider is mock.
2. Fix the registered Agent client’s Authorization header before using regular filesystem-agent enrollment/sync.
3. Fix customer-segment precedence and make the integration test pass. Until then, don’t claim the dashboard correctly identifies declining customers.
4. If you want to show approval simulation, arrange a clearly identified, safe pending demo action; it is not created by the seed.

**Frontend improvements that would make the story much clearer:**

- A real **watcher/worker status panel** with agent online/last-seen, last file/event, latest ingestion result, and API/database/LLM health. There is no Agent page today; use watcher terminal logs and API docs for status.
- Show the **tenant name** consistently. Several pages currently display the tenant UUID where Overview displays “Meridian Transport.”
- Improve the forecast chart to show historical actuals alongside forecasts and, if available, uncertainty bands.
- Add a seeded, explicitly demo-only recommendation card so the approval simulation is reliably present in rehearsals.

The website and routes are in `frontend/templates` and `app/web/routes`; the dashboard currently doesn’t poll for live watcher updates.





# Meridian Transport rehearsal flow

## 1. Start the platform

Open terminals from `C:\Users\LocalAdmin\Documents\platform`.

**Terminal 1 — database and migrations:**

```powershell
make db-up
make migrate
```

**Terminal 2 — API:**

```powershell
make dev
```

**Terminal 3 — worker:**

```powershell
make worker
```

The worker is part of the platform setup, though this demo’s agent-file ingestion is triggered by the watcher’s upload request.

## 2. Prepare the manager and watcher

Do this setup once for the demo. Choose and keep the manager’s email and password; rerunning the setup won’t reset an existing password.

From the platform folder:

```powershell
$env:DEMO_ADMIN_EMAIL = "your-demo-email@example.test"
$env:DEMO_ADMIN_PASSWORD = "<your-chosen-password>"
uv run python -m scripts.seed_demo
uv run python -m scripts.enroll_demo_agent
```

Copy the one-time enrollment token printed by the second command. Then clear the credentials:

```powershell
Remove-Item Env:DEMO_ADMIN_EMAIL, Env:DEMO_ADMIN_PASSWORD
```

In another terminal, enroll and start the watcher:

```powershell
cd C:\Users\LocalAdmin\Documents\sansa-agent
uv sync --dev
$token = Read-Host "Paste the one-time enrollment token"
uv run sansa-agent enroll `
  --server-url http://127.0.0.1:8000 `
  --enrollment-token $token `
  --watch-root C:\Users\LocalAdmin\Documents\platform\data\demo\meridian_transport
Remove-Variable token
uv run sansa-agent run --interval 5
```

Keep this terminal open. The watcher scans the folder and reports file metadata and SHA-256 hashes. Enrollment is one-time: don’t create a second agent if one is already registered.

## 3. Open the source records in Excel

The files are in `data/demo/meridian_transport/`. Open a few in Excel before logging in:

- `Daily operations`
- `Fuel records`
- `Supplier records`
- `Ticket transactions`
- `Customer transactions`
- `Route performance`

**Say:**

> “I’m presenting as the manager of Meridian Transport. Before I show you a dashboard number, I want to show you the source records it comes from. These are synthetic demonstration records—not real customer data—but the platform will process these files through the same watcher and ingestion path.”

Point out the checkable values: passenger counts and ticket revenue rise across the three named routes; fuel use ends at **11.80 L/100 km**; supplier lead time reaches **18 days** after earlier values near **3 days**; and the final operations row shows **18 hours of downtime**.

## 4. Show the manager view and the data path

Open `http://127.0.0.1:8000` and sign in using the demo email and password. The tenant slug is `meridian-transport-demo`.

**Say:**

> “This is my management view. The banner identifies the demo environment, and the data panel shows whether each source file has actually been seen and ingested.”

Point to **From source file to business signal**. Before ingestion, the files should show as waiting for the watcher scan or as seen by the watcher. Once seen, the panel displays each filename and a prefix of its reported hash.

## 5. Ask the watcher to deliver the files

On the overview, click **Ask watcher to upload**. The running watcher terminal should log the individual file uploads, including byte counts and hash prefixes. Return to the overview and refresh to see the per-file ingestion status and staged row count.

**Say:**

> “The watcher first reported file metadata and hashes. I asked the platform to request the file contents, and now you can see the uploads recorded here. These are the same files I just opened in Excel.”

The platform queues one upload job per ready file. The agent sends the bytes, and ingestion parses those uploaded bytes; the provisioning script does **not** load the business rows.

If all files already show as ingested, unchanged files won’t be queued again. Don’t present that as a fresh upload; show the recorded status, or rehearse the first-run flow before the presentation.

## 6. Explain the results using the CSVs

Point to the overview KPIs and signals, then click **Open source record** to show the ingestion jobs, parsed row counts, profiles, and mappings.

**Say:**

> “The figures are calculated from the staged rows that came through the watcher. The original column labels are preserved, and the demo’s data dictionary connects those fields to business concepts the analytics can use.”

For example, the revenue KPI is calculated from mapped revenue columns; customer analysis uses customer, transaction ID, transaction date, and revenue. The integration test verifies **209,310 total revenue**, **12,000 passengers**, and Bluebird Travel’s declining trend against these generated records.

Explain **Semantic understanding** in plain language: Sansa preserves each original column, then matches it to a shared business definition. For example, “Buy Price” is treated as a purchase price. These confirmed matches let analytics use the same business meaning even if two files use different column names. The demo data dictionary is configured during setup; it is **not** live discovery of column meanings during this rehearsal.

The overview now shows transport-relevant metrics only. Inventory measures such as stock by product are intentionally omitted because the transport files contain no stock records; a gross-margin percentage is also omitted because the mapped cost data does not cover all costs needed for a reliable margin.

The checked-in demo pattern now verifies **224,396.64 total ticket and customer revenue** and **12,924 passengers**. The current rehearsal also has a saved four-week passenger forecast; explain it as a projection from 12 weekly observations, not as a guarantee.

## 7. Close with the right caveats

- A newly provisioned tenant does not automatically save a forecast. Generate one before the presentation; this current rehearsal already has a four-week passenger forecast.
- The presentation environment uses `LLM_PROVIDER=mock`. **Ask Sansa** is not live Gemma reasoning here; the mock returns a fixed response. Don’t use it as proof of analysis.
- The anomaly signals and KPI/customer analytics are separate from that mock response and are calculated from ingested data.
- The files are synthetic and intentionally designed to make the trends easy to inspect. Describe them as a demonstration dataset, not as real Meridian operational records.

The setup and presentation steps are also documented in the `Meridian demo section of README.md`.
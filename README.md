# Sansa

AI management system for managers.

Core loop: **Data → Understanding → Decision → Action.**

## Local development setup

### Prerequisites

- Python 3.13+
- Docker Desktop (for Postgres + pgvector)
- PowerShell 7+ recommended on Windows (for `-Form` in test scripts)

The local embedding model is cached persistently at
`~/.cache/sansa/fastembed` (under the current user's home directory), so it is
reused across platform restarts. Set `FASTEMBED_CACHE_DIR` to an absolute path
if you want the cache stored elsewhere; avoid temporary directories.

### Start the API and worker

Run `make dev` from the repository root to start the FastAPI application at
`http://127.0.0.1:8000`. In a separate terminal, run `make worker` to start the
background ingestion and workflow worker. `make dev` does not start the
database, worker, watcher, or language model server.

For a presentation without a configured model server, set `LLM_PROVIDER=mock`
in `.env` and restart the API. This keeps question requests available, but the
mock deliberately returns the fixed text `Mock response`; it does not reason
over the evidence or represent a live LLM answer. Present the saved analytics,
evidence, and deterministic workflow outputs separately, and do not describe
the mock answer as Gemma-generated.

When a GPU endpoint is available, switch `.env` to `LLM_PROVIDER=sglang`.
Gemma 4 26B A4B is served by SGLang on that machine. Sansa sends requests to
the OpenAI-compatible endpoint configured by `LLM_BASE_URL` (default
`http://127.0.0.1:8001/v1`); this is a wire protocol, not a call to OpenAI's
hosted API. The SGLang server is separate and must be running before asking
questions against Gemma.

For a remote GPU machine, keep the default URL and forward its SGLang port
securely from a PowerShell terminal on the platform machine:

```powershell
ssh -N -L 8001:127.0.0.1:8001 <gpu-user>@<gpu-host>
```

Keep the SSH tunnel open. Configure the remote SGLang server to listen on
`127.0.0.1:8001` on the GPU machine, then verify the forwarded endpoint from
the platform machine with
`Invoke-RestMethod http://127.0.0.1:8001/v1/models`; it must list the Gemma
model. If the server requires API authentication, set `LLM_API_KEY` to its
key; for a server without authentication, keep the `INVALID_LOCAL_KEY`
sentinel so Sansa omits the Authorization header.

### 1. Clone and create a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## Redis rate limiting

Set `REDIS_URL` to a shared Redis instance to share route limits across workers.
Development can omit it and uses an in-process limiter; production requires
`REDIS_URL` and verifies Redis connectivity during startup.

## SQL data sources

SQL sources use `credential_ref` values resolved from `SANSA_SECRET_<REF>`.
Credentials must point to PostgreSQL (`postgresql+asyncpg`) or to SQLite with
`mode=ro&uri=true`; PostgreSQL connections run in read-only transactions.
Use a database account with read-only permissions. Queries must be a single
`SELECT`; ingestion applies a configured row cap, statement timeout, and
streamed batches.

## Meridian Transport demo

The Meridian demo presents the signed-in user as the transport manager and
uses six explicitly synthetic, Excel-openable CSV files. Provisioning creates
the tenant, manager, agent source, data dictionary, KPI/detector catalogs, and
industry packs; it does **not** upload files, create staged rows, or calculate
signals. The filesystem watcher reports file metadata and SHA-256 hashes. Only
after an operator requests ingestion does the watcher transmit the bytes and
the platform parse them. The overview shows per-file observation and ingestion
status. On an existing demo tenant, it marks the old `demo_seed_key` CSV
sources inactive but retains their rows and history; those old sources are
excluded from current analytics. Do not present these generated records as a
real customer's data.

The seed script does not include a default password. It writes the six source
files under `data/demo/meridian_transport/` and provisions one agent source
without loading any business rows. Open the CSVs in Excel to inspect the
values before the watcher sends them. The patterns are designed to be
directly checkable:

- weekly operating cost grows 7.5% per period and the last downtime value is
  18 hours;
- the final fuel-use rate is 11.8 L/100 km, above its earlier values;
- supplier lead time ends at 18 days after earlier values near 3 days;
- passenger counts and ticket revenue rise at different rates across the three
  named routes, while customer transaction rows show account-level spending
  and declining customer activity.

### Start and demonstrate the file-to-analysis path

Start the database and apply migrations:

```powershell
make db-up
make migrate
```

Start the API in its own terminal:

```powershell
make dev
```

Start the worker in another terminal:

```powershell
make worker
```

Set the demo user's credentials and provision the demo once:

```powershell
$env:DEMO_ADMIN_EMAIL = "demo@example.test"
$env:DEMO_ADMIN_PASSWORD = "<choose-a-strong-password>"
uv run python -m scripts.seed_demo
uv run python -m scripts.enroll_demo_agent
```

The enrollment command prints a one-time token. Copy it for immediate use;
the platform stores only its hash. Clear the bootstrap credentials in that
terminal, then open a separate watcher terminal:

```powershell
Remove-Item Env:DEMO_ADMIN_EMAIL, Env:DEMO_ADMIN_PASSWORD
```

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

Keep the watcher running. It first reports file names, sizes, and hashes; its
log then shows each content upload with byte count and hash prefix after the
manager clicks **Ask watcher to upload** on the overview. The platform creates
one ingestion job per CSV. The watcher delivers the files quickly; the
separate worker performs parsing, profiling, and analytics. The overview's
progress bar updates from the actual ingestion-job statuses. The platform's
job lineage and semantic mappings are available by opening the watcher source
from the overview.

The analysis can be audited against the CSV values and confirmed mappings.
For example, `sales.total_revenue` is derived from the ticket revenue and
customer transaction revenue columns; customer segments use the mapped
customer, transaction ID, transaction date, and revenue fields. Forecasts use
content dates extracted from each configured CSV date column. The overview
shows transport-relevant metrics instead of inventory KPIs that have no stock
records in this demo. The generated records are synthetic and deterministic
except for their rolling dates.

The local presentation configuration uses `LLM_PROVIDER=mock`; its fixed
`Mock response` is not a live model analysis. Deterministic analytics and
signals are calculated by the platform from the ingested rows, separately from
that mock answer. Forecast questions can infer a numeric measure from the
tenant's confirmed mappings; explicit API forecast requests remain supported.
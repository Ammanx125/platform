# Sansa

AI management system for managers.

Core loop: **Data → Understanding → Decision → Action.**

## Local development setup

### Prerequisites

- Python 3.13+
- Docker Desktop (for Postgres + pgvector)
- PowerShell 7+ recommended on Windows (for `-Form` in test scripts)

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

The demo seed creates the `Meridian Transport` tenant, a tenant administrator,
five synthetic CSV data sources, and a confirmed semantic mapping for each
analytic field. The source rows retain their original column names; the seed
also installs the transport industry pack and produces real KPI, anomaly, and
cost-forecast records from the staged data. The seeded patterns include stable
ticket revenue, worsening fuel use, a supplier delay, and an operational
downtime spike.

Provide credentials explicitly; the seed script does not include a default
password. In PowerShell:

```powershell
$env:DEMO_ADMIN_EMAIL = "demo@example.test"
$env:DEMO_ADMIN_PASSWORD = "<choose-a-strong-password>"
uv run python -m scripts.seed_demo
Remove-Item Env:DEMO_ADMIN_EMAIL, Env:DEMO_ADMIN_PASSWORD
```

The script is safe to rerun: it reuses the tenant and sources, does not reset an
existing user's password, and avoids duplicating completed ingestion jobs or
successful forecasts. It writes the synthetic CSV content through the configured
local upload storage and ingests it with the standard CSV connector.
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
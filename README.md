# Sansa

AI management system for managers.

Core loop: **Data → Understanding → Decision → Action.**

## Local development setup

### Prerequisites

- Python 3.12+
- Docker Desktop (for Postgres + pgvector)
- PowerShell 7+ recommended on Windows (for `-Form` in test scripts)

### 1. Clone and create a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
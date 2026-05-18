# Project notes for Claude

## psql access

There is **no native PostgreSQL install** on this machine — `psql.exe` is not
on PATH and `C:\Program Files\PostgreSQL\` does not exist. The database runs
in Docker as container `taxmind-postgres` (image `postgres:16-alpine`,
published on `0.0.0.0:5432`).

To run `psql` against the project DB, exec into the container:

```powershell
docker exec -it taxmind-postgres psql -U taxmind -d taxmind_books
```

For one-shot queries (no `-it` so output is capturable):

```powershell
docker exec taxmind-postgres psql -U taxmind -d taxmind_books -c "SELECT 1;"
```

Credentials come from `backend/.env` (`DATABASE_URL`). The in-container
`psql` binary lives at `/usr/local/bin/psql` (PostgreSQL 16.13).

For Python-side queries that need ORM access, prefer the backend's
`SessionLocal`:

```powershell
cd "H:\Accounting Project\backend"
.venv/Scripts/python -c "from app.core.database import SessionLocal; from sqlalchemy import text; s=SessionLocal(); print(s.execute(text('SELECT 1')).scalar()); s.close()"
```

## Backend health endpoint

Liveness probe is `GET /health` (unprefixed, **not** `/api/v1/health`).
Returns `{"status":"ok","env":"<APP_ENV>"}`. Defined in
`backend/app/main.py`. `/api/v1/health/ready` is documented in
`docs/API.md` but is not implemented in Phase 0.

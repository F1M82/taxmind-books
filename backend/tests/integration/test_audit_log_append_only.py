"""Append-only enforcement test for audit_logs.

Per `docs/AUDIT.md` Layer 2: the database refuses UPDATE and DELETE on
`audit_logs`. This test runs the migration against a real Postgres,
inserts a row, then asserts that UPDATE and DELETE both raise.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError, OperationalError


def _alembic_cfg() -> Config:
    backend = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _database_url())
    return cfg


def _database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or os.environ["DATABASE_URL"]


@pytest.fixture
def db_or_skip() -> str:
    url = _database_url()
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except (OperationalError, Exception) as exc:
        pytest.skip(f"Postgres not reachable at {url!s}: {exc}")
    return url


@pytest.fixture
def migrated_db(db_or_skip: str) -> str:
    """Drop everything, run all migrations to head."""
    engine = create_engine(db_or_skip)
    with engine.begin() as conn:
        conn.execute(
            text("DROP TABLE IF EXISTS account_deletion_requests CASCADE")
        )
        conn.execute(text("DROP TABLE IF EXISTS device_tokens CASCADE"))
        conn.execute(
            text("DROP TABLE IF EXISTS connector_enrollment_codes CASCADE")
        )
        conn.execute(text("DROP TABLE IF EXISTS idempotency_keys CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS audit_logs CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS ledger_entries CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS vouchers CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS ledgers CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS user_companies CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS companies CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
        conn.execute(
            text("DROP TYPE IF EXISTS account_deletion_status CASCADE")
        )
        conn.execute(text("DROP TYPE IF EXISTS device_platform CASCADE"))
        conn.execute(text("DROP TYPE IF EXISTS entry_type CASCADE"))
        conn.execute(text("DROP TYPE IF EXISTS voucher_status CASCADE"))
        conn.execute(text("DROP TYPE IF EXISTS voucher_type CASCADE"))
        conn.execute(text("DROP TYPE IF EXISTS balance_type CASCADE"))
        conn.execute(text("DROP TYPE IF EXISTS company_status CASCADE"))
        conn.execute(text("DROP TYPE IF EXISTS company_role CASCADE"))
        conn.execute(
            text("DROP FUNCTION IF EXISTS prevent_audit_modification() CASCADE")
        )
        conn.execute(text("DROP FUNCTION IF EXISTS set_updated_at() CASCADE"))
    engine.dispose()
    command.upgrade(_alembic_cfg(), "head")
    return db_or_skip


def _seed_company_and_log(engine_url: str) -> tuple[str, str]:
    """Insert a company + an audit_logs row; return (company_id, audit_id)."""
    engine = create_engine(engine_url)
    with engine.begin() as conn:
        company_id = conn.execute(
            text(
                "INSERT INTO companies (name) VALUES ('Acme Co') "
                "RETURNING id::text"
            )
        ).scalar_one()
        audit_id = conn.execute(
            text(
                "INSERT INTO audit_logs "
                "(company_id, action, entity_type, entity_id, source) "
                "VALUES (:cid, 'voucher.created', 'voucher', "
                "gen_random_uuid(), 'api') "
                "RETURNING id::text"
            ),
            {"cid": company_id},
        ).scalar_one()
    engine.dispose()
    return company_id, audit_id


@pytest.mark.integration
def test_audit_log_update_is_blocked(migrated_db: str) -> None:
    _, audit_id = _seed_company_and_log(migrated_db)
    engine = create_engine(migrated_db)
    with engine.begin() as conn, pytest.raises(DatabaseError) as exc_info:
        conn.execute(
            text("UPDATE audit_logs SET action = 'tampered' WHERE id = :id"),
            {"id": audit_id},
        )
    assert "append-only" in str(exc_info.value).lower()
    engine.dispose()


@pytest.mark.integration
def test_audit_log_delete_is_blocked(migrated_db: str) -> None:
    _, audit_id = _seed_company_and_log(migrated_db)
    engine = create_engine(migrated_db)
    with engine.begin() as conn, pytest.raises(DatabaseError) as exc_info:
        conn.execute(
            text("DELETE FROM audit_logs WHERE id = :id"),
            {"id": audit_id},
        )
    assert "append-only" in str(exc_info.value).lower()
    engine.dispose()


@pytest.mark.integration
def test_audit_log_insert_is_allowed(migrated_db: str) -> None:
    """Sanity: append-only means INSERT works."""
    company_id, _ = _seed_company_and_log(migrated_db)
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        n_before = conn.execute(
            text("SELECT COUNT(*) FROM audit_logs WHERE company_id = :cid"),
            {"cid": company_id},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO audit_logs "
                "(company_id, action, entity_type, entity_id, source) "
                "VALUES (:cid, 'company.updated', 'company', :cid, 'api')"
            ),
            {"cid": company_id},
        )
        n_after = conn.execute(
            text("SELECT COUNT(*) FROM audit_logs WHERE company_id = :cid"),
            {"cid": company_id},
        ).scalar_one()
    assert n_after == n_before + 1
    engine.dispose()

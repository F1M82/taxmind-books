"""Alembic upgrade/downgrade round-trip integration test.

Skipped when no live Postgres is reachable. When it runs, it walks
upgrade head → downgrade base → upgrade head, asserting the round-trip
is clean and the expected tables come and go.

The test honors `TEST_DATABASE_URL` if set, otherwise falls back to the
process `DATABASE_URL`. CI provides a real Postgres service in P0.32.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError


def _alembic_cfg() -> Config:
    backend = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _database_url())
    return cfg


def _database_url() -> str:
    return (
        os.environ.get("TEST_DATABASE_URL")
        or os.environ["DATABASE_URL"]
    )


@pytest.fixture
def db_or_skip() -> str:
    url = _database_url()
    # Short connect_timeout so a missing local Postgres skips fast
    # instead of waiting psycopg's multi-minute default.
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except (OperationalError, Exception) as exc:
        pytest.skip(f"Postgres not reachable at {url!s}: {exc}")
    return url


@pytest.fixture
def clean_db(db_or_skip: str) -> str:
    """Drop the alembic schema artifacts so the round-trip starts clean."""
    engine = create_engine(db_or_skip)
    with engine.begin() as conn:
        # Drop tables and types added by every migration to date.
        # New migrations that create their own tables/enums must
        # extend this list — otherwise the round-trip test wedges on
        # `type "X" already exists` after a partial-run regression.
        # The same hazard is documented in docs/SCHEMA.sql for
        # voucher_status; the rule applies to every enum.
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
    return db_or_skip


@pytest.mark.integration
def test_alembic_upgrade_creates_initial_tables(clean_db: str) -> None:
    cfg = _alembic_cfg()
    command.upgrade(cfg, "head")

    engine = create_engine(clean_db)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "users",
        "companies",
        "user_companies",
        "ledgers",
        "vouchers",
        "ledger_entries",
        "audit_logs",
        "idempotency_keys",
    }.issubset(tables)

    # Triggers attached
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT trigger_name FROM information_schema.triggers "
                "WHERE trigger_name LIKE 'trg_%_updated_at'"
            )
        )
        trigger_names = {row[0] for row in result}
    assert {
        "trg_users_updated_at",
        "trg_companies_updated_at",
        "trg_user_companies_updated_at",
        "trg_ledgers_updated_at",
        "trg_vouchers_updated_at",
    }.issubset(trigger_names)

    # gin trigram index
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE indexname = 'idx_ledgers_name_trgm'"
            )
        )
        assert result.scalar() == "idx_ledgers_name_trgm"

    # voucher_number uniqueness is DEFERRABLE INITIALLY DEFERRED
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT condeferrable, condeferred FROM pg_constraint "
                "WHERE conname = 'uq_vouchers_company_number_type'"
            )
        ).one()
        assert result.condeferrable is True
        assert result.condeferred is True
    engine.dispose()


@pytest.mark.integration
def test_alembic_downgrade_then_upgrade_is_clean(clean_db: str) -> None:
    cfg = _alembic_cfg()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(clean_db)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for t in (
        "users",
        "companies",
        "user_companies",
        "ledgers",
        "vouchers",
        "ledger_entries",
        "audit_logs",
        "idempotency_keys",
    ):
        assert t not in tables
    engine.dispose()

    # Re-upgrade must succeed without orphan-state errors.
    command.upgrade(cfg, "head")
    engine = create_engine(clean_db)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "users",
        "companies",
        "user_companies",
        "ledgers",
        "vouchers",
        "ledger_entries",
        "audit_logs",
        "idempotency_keys",
    }.issubset(tables)
    engine.dispose()

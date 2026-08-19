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
        conn.execute(text("DROP TABLE IF EXISTS connector_company_bindings CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS tally_companies_discovered CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS connectors CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS idempotency_keys CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS audit_logs CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS ledger_entries CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS vouchers CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS ledgers CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS user_companies CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS companies CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
        conn.execute(text("DROP TYPE IF EXISTS alembic_version CASCADE"))
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

    # P3.7: the durable voucher identity is now the partial unique index
    # (company_id, tally_guid), NOT (company_id, voucher_type, voucher_number).
    with engine.connect() as conn:
        old = conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conname = 'uq_vouchers_company_number_type'"
            )
        ).first()
        assert old is None  # old constraint is gone
    # New partial unique index must exist and be unique.
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_vouchers_company_tally_guid'"
            )
        ).one()
        assert row.indexname == "uq_vouchers_company_tally_guid"
        assert "UNIQUE" in row.indexdef
        assert "tally_guid IS NOT NULL" in row.indexdef
    # Vouchers table carries the four P3.7 Tally identity columns.
    voucher_cols = {c["name"] for c in inspector.get_columns("vouchers")}
    assert {
        "tally_guid",
        "tally_master_id",
        "tally_vchkey",
        "tally_alter_id",
    }.issubset(voucher_cols)
    # P3.7 Phase 6A: Tally-origin metadata columns.
    assert {
        "tally_voucher_type",
        "tally_is_cancelled",
        "tally_is_deleted",
        "tally_is_optional",
    }.issubset(voucher_cols)
    # P3.7 Phase 7B: companies.tally_master_id (Tally company GUID) with a
    # global unique constraint (defense-in-depth against cross-company reuse).
    company_cols = {c["name"] for c in inspector.get_columns("companies")}
    assert "tally_master_id" in company_cols
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conname = 'uq_companies_tally_master_id'"
            )
        ).one()
        assert row.conname == "uq_companies_tally_master_id"
        idx = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE indexname = 'idx_companies_tally_master_id'"
            )
        ).one()
        assert idx.indexname == "idx_companies_tally_master_id"
    engine.dispose()


@pytest.mark.integration
def test_alembic_0014_import_metadata_and_downgrade(clean_db: str) -> None:
    """0014 makes voucher_type nullable and adds the Tally-origin columns.

    Downgrade must drop the four new columns and restore voucher_type
    NOT NULL (safe — no imported row has a NULL type yet)."""
    cfg = _alembic_cfg()
    command.upgrade(cfg, "head")

    engine = create_engine(clean_db)
    inspector = inspect(engine)
    voucher_cols = {c["name"]: c for c in inspector.get_columns("vouchers")}
    assert voucher_cols["voucher_type"]["nullable"] is True
    for name in (
        "tally_voucher_type",
        "tally_is_cancelled",
        "tally_is_deleted",
        "tally_is_optional",
    ):
        assert name in voucher_cols
        assert voucher_cols[name]["nullable"] is True
    engine.dispose()

    command.downgrade(cfg, "0013")

    engine = create_engine(clean_db)
    voucher_cols = {c["name"]: c for c in inspect(engine).get_columns("vouchers")}
    assert voucher_cols["voucher_type"]["nullable"] is False
    for name in (
        "tally_voucher_type",
        "tally_is_cancelled",
        "tally_is_deleted",
        "tally_is_optional",
    ):
        assert name not in voucher_cols
    engine.dispose()

    # Re-upgrade must land cleanly.
    command.upgrade(cfg, "head")


@pytest.mark.integration
def test_alembic_p37_partial_downgrade_restores_old_identity(clean_db: str) -> None:
    """Migration 0013 downgrade must restore the EXACT old constraint.

    Before any Tally GUID data exists (all tally_guid NULL), downgrading
    0013 → 0011 must drop the partial index and recreate
    `uq_vouchers_company_number_type` as DEFERRABLE INITIALLY DEFERRED —
    exactly as it was before P3.7. Re-upgrade must then re-apply the
    P3.7 identity cleanly (GATE 2/3, Phase 5 item 9/10).
    """
    cfg = _alembic_cfg()
    command.upgrade(cfg, "head")

    # Downgrade only the two P3.7 migrations.
    command.downgrade(cfg, "0011")

    engine = create_engine(clean_db)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT condeferrable, condeferred FROM pg_constraint "
                "WHERE conname = 'uq_vouchers_company_number_type'"
            )
        ).one()
        assert result.condeferrable is True
        assert result.condeferred is True
        # Partial index is gone.
        idx = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE indexname = 'uq_vouchers_company_tally_guid'"
            )
        ).first()
        assert idx is None
    # The four P3.7 columns must be gone at 0011.
    voucher_cols = {c["name"] for c in inspect(engine).get_columns("vouchers")}
    assert not {
        "tally_guid",
        "tally_master_id",
        "tally_vchkey",
        "tally_alter_id",
    }.issubset(voucher_cols)
    engine.dispose()

    # Re-upgrade to head — the new identity must land cleanly.
    command.upgrade(cfg, "head")
    engine = create_engine(clean_db)
    with engine.connect() as conn:
        old = conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conname = 'uq_vouchers_company_number_type'"
            )
        ).first()
        assert old is None
        row = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_vouchers_company_tally_guid'"
            )
        ).one()
        assert "tally_guid IS NOT NULL" in row.indexdef
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

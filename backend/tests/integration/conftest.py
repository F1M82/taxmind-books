"""Shared fixtures for integration tests that need a live Postgres.

The session-scoped `migrated_engine` runs all alembic migrations once
per test session. Per-function fixtures (`db_session`, `client`,
factory functions) build on top of it.

Tests that don't have Postgres available skip cleanly via the
``db_or_skip`` pattern: importing this module is safe even when no
Postgres is up; only fixtures that *use* the engine fail/skip.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.api import deps
from app.core.database import SessionLocal, engine
from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models.company import Company, CompanyRole, UserCompany
from app.models.user import User
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


def _alembic_cfg() -> Config:
    backend = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _database_url())
    return cfg


def _database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or os.environ["DATABASE_URL"]


@pytest.fixture(scope="session")
def migrated_engine() -> Generator[str, None, None]:
    """Bring the test DB to head once per session, then tear down."""
    url = _database_url()
    try:
        probe = create_engine(url, connect_args={"connect_timeout": 2})
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        probe.dispose()
    except (OperationalError, Exception) as exc:
        pytest.skip(f"Postgres not reachable at {url!s}: {exc}")

    # Drop everything so we start clean.
    e = create_engine(url)
    with e.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS idempotency_keys CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS audit_logs CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS ledger_entries CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS vouchers CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS ledgers CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS user_companies CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS companies CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
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
    e.dispose()

    # The app's engine is created at module import from $DATABASE_URL.
    # Tests must therefore set DATABASE_URL to the test DB before
    # importing app.* — see top-level conftest.py + the test runner
    # invocation. We assert it here to fail loudly if a developer runs
    # the integration suite against a non-test DB by mistake.
    # Compare URL components rather than str() because SQLAlchemy masks
    # the password as '***' in the rendered string.
    from sqlalchemy.engine import make_url

    expected = make_url(url)
    actual = engine.url
    assert (actual.host, actual.port, actual.database, actual.username) == (
        expected.host,
        expected.port,
        expected.database,
        expected.username,
    ), (
        f"app.core.database.engine is bound to {engine.url!r}, "
        f"but tests target {url!r}. Set DATABASE_URL to the test DB."
    )

    command.upgrade(_alembic_cfg(), "head")
    yield url


@pytest.fixture
def db_session(migrated_engine: str) -> Generator[Session, None, None]:
    """A function-scoped session. The test owns the transaction."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _reset_tenancy_tables(migrated_engine: str) -> Generator[None, None, None]:
    """Truncate per-test so factories don't collide on uniqueness."""
    yield
    e = create_engine(migrated_engine)
    with e.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE idempotency_keys, audit_logs, "
                "ledger_entries, vouchers, ledgers, user_companies, "
                "companies, users RESTART IDENTITY CASCADE"
            )
        )
    e.dispose()


# ---------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------


def make_user(
    db: Session,
    *,
    email: str | None = None,
    password: str = "hunter2-pwd",
    full_name: str = "Test User",
    is_active: bool = True,
) -> User:
    user = User(
        email=email or f"user-{uuid4().hex[:8]}@example.com",
        hashed_password=hash_password(password),
        full_name=full_name,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_company(
    db: Session,
    *,
    name: str | None = None,
    status: str = "active",
) -> Company:
    company = Company(
        name=name or f"Co-{uuid4().hex[:6]}",
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    if status != "active":
        db.execute(
            text(
                "UPDATE companies SET status = :s WHERE id = :id"
            ),
            {"s": status, "id": str(company.id)},
        )
        db.commit()
        db.refresh(company)
    return company


def make_membership(
    db: Session,
    user: User,
    company: Company,
    role: CompanyRole = CompanyRole.viewer,
) -> UserCompany:
    membership = UserCompany(
        user_id=user.id,
        company_id=company.id,
        role=role,
    )
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return membership


def issue_token(user: User) -> str:
    return create_access_token(user.id)


# ---------------------------------------------------------------------
# Test app + client
# ---------------------------------------------------------------------


def build_test_app() -> FastAPI:
    """An app augmented with probe routes that exercise each dependency.

    Kept inside this conftest so tenancy tests can import a single
    fixture and avoid duplicating route boilerplate.
    """
    from fastapi import Depends

    app = create_app()

    @app.get("/_probe/whoami")
    def whoami(user: User = Depends(deps.get_current_user)) -> dict[str, str]:
        return {"id": str(user.id), "email": user.email}

    @app.get("/_probe/active-company")
    def active_company(
        company: Company = Depends(deps.get_active_company),
    ) -> dict[str, str]:
        return {"id": str(company.id), "name": company.name}

    @app.get(
        "/_probe/owner-only",
        dependencies=[Depends(deps.require_role("owner"))],
    )
    def owner_only() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/_probe/owner-or-admin")
    def owner_or_admin(
        company: Company = Depends(deps.require_role("owner", "admin")),
    ) -> dict[str, str]:
        return {"company_id": str(company.id)}

    @app.get("/_probe/scoped-list-ledgers")
    def scoped_list_ledgers(
        db: Session = Depends(deps.get_scoped_session),
    ) -> dict[str, list[str]]:
        from app.models.ledger import Ledger

        rows = db.query(Ledger).all()
        return {"names": [r.name for r in rows]}

    # ----------- Idempotency probe endpoints -----------
    from app.core.idempotency import IdempotencyHandler

    @app.post("/_probe/idem-required", status_code=201)
    async def idem_required(
        body: dict,
        db: Session = Depends(deps.get_scoped_session),
        idem: IdempotencyHandler = Depends(deps.get_idempotency_handler),
    ):  # type: ignore[no-untyped-def]
        replay = await idem.check(required=True)
        if replay is not None:
            return replay
        result = {"echo": body, "created_id": str(uuid4())}
        idem.store_response(status_code=201, body=result)
        db.commit()
        return result

    @app.post("/_probe/idem-required-other", status_code=201)
    async def idem_required_other(
        body: dict,
        db: Session = Depends(deps.get_scoped_session),
        idem: IdempotencyHandler = Depends(deps.get_idempotency_handler),
    ):  # type: ignore[no-untyped-def]
        replay = await idem.check(required=True)
        if replay is not None:
            return replay
        result = {"other": True, "echo": body}
        idem.store_response(status_code=201, body=result)
        db.commit()
        return result

    return app


@pytest.fixture
def client(migrated_engine: str) -> Generator[TestClient, None, None]:
    """FastAPI test client with the probe routes attached."""
    app = build_test_app()
    with TestClient(app) as c:
        yield c


# Re-export helpers as fixture-shaped functions so tests can ergonomically
# do `make_user(db_session, email=...)`.
@pytest.fixture
def factory_user():  # type: ignore[no-untyped-def]
    return make_user


@pytest.fixture
def factory_company():  # type: ignore[no-untyped-def]
    return make_company


@pytest.fixture
def factory_membership():  # type: ignore[no-untyped-def]
    return make_membership


@pytest.fixture
def auth_header():  # type: ignore[no-untyped-def]
    """Return a function `(user) -> {Authorization: Bearer <jwt>}`."""

    def _hdr(user: User, *, company_id: UUID | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {issue_token(user)}"}
        if company_id is not None:
            h["X-Company-ID"] = str(company_id)
        return h

    return _hdr


# Mark `now` symbol so unused-import linters don't trip — keeps datetime
# importable for tests that re-use it without re-importing.
_ = datetime.now(UTC)

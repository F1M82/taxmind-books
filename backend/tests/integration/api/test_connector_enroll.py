"""Integration tests for connector enrollment (P0.23)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.security import (
    CONNECTOR_TOKEN_DEFAULT_EXPIRE_DAYS,
    decode_connector_token,
)
from app.models.company import CompanyRole
from app.models.connector_enrollment import ConnectorEnrollmentCode
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)


def _h(user, company) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }


# ---------------- POST /enrollment-codes ----------------


def test_owner_can_issue_code(client: TestClient, db_session: Session) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    r = client.post(
        "/api/v1/connector/enrollment-codes",
        headers=_h(user, company),
    )
    assert r.status_code == 201
    body = r.json()
    assert len(body["code"]) >= 30
    UUID(body["company_id"])

    db_session.expire_all()
    rows = db_session.query(ConnectorEnrollmentCode).all()
    assert len(rows) == 1
    # The raw code is NOT stored; the row holds only its hash.
    import hashlib

    assert (
        rows[0].code_hash
        == hashlib.sha256(body["code"].encode()).hexdigest()
    )


def test_admin_cannot_issue_code(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.admin)
    r = client.post(
        "/api/v1/connector/enrollment-codes", headers=_h(user, company)
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "insufficient_role"


def test_non_member_cannot_issue_code(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    # No membership.
    r = client.post(
        "/api/v1/connector/enrollment-codes", headers=_h(user, company)
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "company_not_found"


# ---------------- POST /enroll ----------------


def test_enroll_returns_connector_token(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    issued = client.post(
        "/api/v1/connector/enrollment-codes",
        headers=_h(user, company),
    ).json()

    r = client.post("/api/v1/connector/enroll", json={"code": issued["code"]})
    assert r.status_code == 200, r.json()
    body = r.json()
    UUID(body["connector_id"])
    assert body["company_id"] == str(company.id)
    assert body["expires_in_days"] == CONNECTOR_TOKEN_DEFAULT_EXPIRE_DAYS

    payload = decode_connector_token(body["connector_token"])
    assert payload.company_id == str(company.id)
    assert payload.sub == body["connector_id"]


def test_enroll_marks_code_consumed(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    issued = client.post(
        "/api/v1/connector/enrollment-codes",
        headers=_h(user, company),
    ).json()

    r1 = client.post(
        "/api/v1/connector/enroll", json={"code": issued["code"]}
    )
    assert r1.status_code == 200
    # Second exchange must fail.
    r2 = client.post(
        "/api/v1/connector/enroll", json={"code": issued["code"]}
    )
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "enrollment_code_consumed"


def test_enroll_rejects_unknown_code(client: TestClient) -> None:
    r = client.post(
        "/api/v1/connector/enroll", json={"code": "definitely-not-a-real-code"}
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "enrollment_code_not_found"


def test_enroll_rejects_expired_code(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    issued = client.post(
        "/api/v1/connector/enrollment-codes",
        headers=_h(user, company),
    ).json()

    # Hand-age the row.
    row = (
        db_session.query(ConnectorEnrollmentCode)
        .filter(ConnectorEnrollmentCode.consumed_at.is_(None))
        .one()
    )
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    r = client.post(
        "/api/v1/connector/enroll", json={"code": issued["code"]}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "enrollment_code_expired"


def test_enroll_does_not_require_auth(client: TestClient) -> None:
    """The code itself authenticates the connector. No bearer needed."""
    r = client.post("/api/v1/connector/enroll", json={"code": "anything"})
    # Returns 404 (code not found) — not 401.
    assert r.status_code == 404

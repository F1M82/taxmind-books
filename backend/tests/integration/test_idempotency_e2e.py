"""End-to-end idempotency tests against the probe endpoints.

Exercises every branch of `IdempotencyHandler.check()`:

  * required + missing key  → 400 idempotency_key_required
  * malformed key           → 400 idempotency_key_invalid
  * first request           → 201 + row created
  * replay same key + body  → stored response returned with
                              `Idempotent-Replay: true`
  * replay same key + diff body → 409 idempotency_replay
  * same key on diff path   → 409 idempotency_key_misuse
  * stale lock takeover     → second request proceeds
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.idempotency import LOCK_TIMEOUT
from app.models.company import CompanyRole
from app.models.idempotency_key import IdempotencyKey
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.integration.conftest import (
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


# ---------------- happy path ----------------


def test_first_request_succeeds_and_creates_row(
    client: TestClient, db_session: Session
) -> None:
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.viewer)

    key = "0011aabb-2233-4455-6677-8899aabbccdd"
    r = client.post(
        "/_probe/idem-required",
        headers={**_h(u, c), "Idempotency-Key": key},
        json={"x": 1},
    )
    assert r.status_code == 201

    rows = db_session.query(IdempotencyKey).filter_by(key=key).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.company_id == c.id
    assert row.user_id == u.id
    assert row.method == "POST"
    assert row.path == "/_probe/idem-required"
    assert row.completed_at is not None
    assert row.response_status == 201


def test_replay_returns_stored_response(
    client: TestClient, db_session: Session
) -> None:
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.viewer)

    key = "replay-key-aaaa-bbbb-cccc-dddd-1234"
    body = {"x": 1, "y": 2}
    r1 = client.post(
        "/_probe/idem-required",
        headers={**_h(u, c), "Idempotency-Key": key},
        json=body,
    )
    assert r1.status_code == 201
    first = r1.json()

    r2 = client.post(
        "/_probe/idem-required",
        headers={**_h(u, c), "Idempotency-Key": key},
        json=body,
    )
    assert r2.status_code == 201
    assert r2.json() == first
    assert r2.headers.get("Idempotent-Replay") == "true"


def test_replay_with_reordered_keys_still_matches(
    client: TestClient, db_session: Session
) -> None:
    """Body hash is canonical — key order must not produce a mismatch."""
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.viewer)

    key = "canon-key-1111-2222-3333-4444-aaaa"
    r1 = client.post(
        "/_probe/idem-required",
        headers={**_h(u, c), "Idempotency-Key": key},
        json={"a": 1, "b": 2},
    )
    assert r1.status_code == 201

    r2 = client.post(
        "/_probe/idem-required",
        headers={**_h(u, c), "Idempotency-Key": key},
        json={"b": 2, "a": 1},
    )
    assert r2.status_code == 201
    assert r2.headers.get("Idempotent-Replay") == "true"


# ---------------- missing / invalid key ----------------


def test_missing_key_when_required_is_400(
    client: TestClient, db_session: Session
) -> None:
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.viewer)

    r = client.post(
        "/_probe/idem-required", headers=_h(u, c), json={"x": 1}
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "idempotency_key_required"


def test_malformed_key_is_400(
    client: TestClient, db_session: Session
) -> None:
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.viewer)

    r = client.post(
        "/_probe/idem-required",
        headers={**_h(u, c), "Idempotency-Key": "short"},
        json={"x": 1},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "idempotency_key_invalid"


# ---------------- mismatch cases ----------------


def test_same_key_different_body_returns_409_replay(
    client: TestClient, db_session: Session
) -> None:
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.viewer)

    key = "mismatch-key-1111-2222-3333-4444"
    r1 = client.post(
        "/_probe/idem-required",
        headers={**_h(u, c), "Idempotency-Key": key},
        json={"x": 1},
    )
    assert r1.status_code == 201

    r2 = client.post(
        "/_probe/idem-required",
        headers={**_h(u, c), "Idempotency-Key": key},
        json={"x": 999},  # different body
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["error"]["code"] == "idempotency_replay"


def test_same_key_different_path_returns_409_misuse(
    client: TestClient, db_session: Session
) -> None:
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.viewer)

    key = "misuse-key-1111-2222-3333-4444-bb"
    r1 = client.post(
        "/_probe/idem-required",
        headers={**_h(u, c), "Idempotency-Key": key},
        json={"x": 1},
    )
    assert r1.status_code == 201

    r2 = client.post(
        "/_probe/idem-required-other",
        headers={**_h(u, c), "Idempotency-Key": key},
        json={"x": 1},
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["error"]["code"] == "idempotency_key_misuse"


# ---------------- scope boundary ----------------


def test_same_key_in_different_companies_does_not_collide(
    client: TestClient, db_session: Session
) -> None:
    u = make_user(db_session)
    a = make_company(db_session)
    b = make_company(db_session)
    make_membership(db_session, u, a, role=CompanyRole.viewer)
    make_membership(db_session, u, b, role=CompanyRole.viewer)

    key = "shared-key-aaaa-bbbb-cccc-dddd"
    r_a = client.post(
        "/_probe/idem-required",
        headers={**_h(u, a), "Idempotency-Key": key},
        json={"x": 1},
    )
    r_b = client.post(
        "/_probe/idem-required",
        headers={**_h(u, b), "Idempotency-Key": key},
        json={"x": 1},
    )
    assert r_a.status_code == 201
    assert r_b.status_code == 201
    # Each company saw a fresh first-time request.
    assert r_a.headers.get("Idempotent-Replay") is None
    assert r_b.headers.get("Idempotent-Replay") is None


# ---------------- stale lock takeover ----------------


def test_stale_lock_takeover(
    client: TestClient, db_session: Session
) -> None:
    """A row that's locked but never completed lets the next request proceed
    once `locked_at` is older than `LOCK_TIMEOUT`."""
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.viewer)

    key = "stale-lock-1111-2222-3333-4444-cc"
    # Hand-craft a stuck "in progress" row.
    long_ago = datetime.now(UTC) - LOCK_TIMEOUT - timedelta(seconds=30)
    stuck = IdempotencyKey(
        company_id=c.id,
        user_id=u.id,
        key=key,
        method="POST",
        path="/_probe/idem-required",
        request_hash="placeholder-not-the-real-hash",
        locked_at=long_ago,
        completed_at=None,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(stuck)
    db_session.commit()

    r = client.post(
        "/_probe/idem-required",
        headers={**_h(u, c), "Idempotency-Key": key},
        json={"x": 1},
    )
    assert r.status_code == 201

    # The same row now has a completed_at and the new request_hash.
    db_session.expire_all()
    row = db_session.query(IdempotencyKey).filter_by(key=key).one()
    assert row.completed_at is not None
    assert row.request_hash != "placeholder-not-the-real-hash"


# ---------------- in-progress (lock active) ----------------


def test_in_progress_returns_409(
    client: TestClient, db_session: Session
) -> None:
    """A second request arriving while the first is still locked returns
    409 idempotency_in_progress with Retry-After."""
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.viewer)

    key = "in-progress-key-1111-2222-3333-44"
    fresh_lock = datetime.now(UTC) - timedelta(seconds=5)  # well within LOCK_TIMEOUT
    pending = IdempotencyKey(
        company_id=c.id,
        user_id=u.id,
        key=key,
        method="POST",
        path="/_probe/idem-required",
        request_hash="placeholder",
        locked_at=fresh_lock,
        completed_at=None,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(pending)
    db_session.commit()

    r = client.post(
        "/_probe/idem-required",
        headers={**_h(u, c), "Idempotency-Key": key},
        json={"x": 1},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "idempotency_in_progress"
    assert r.headers.get("Retry-After") == "5"

"""BUG-Books-001 root-cause characterization: PowerShell BOM body + duplicate.

Background
----------
During §7.5a bring-up (2026-05-18) a PowerShell-driven voucher POST was
recorded as producing a "duplicate create". The filed hypothesis
(BUG-Books-001 candidate 1) was that a BOM in the request body made
FastAPI return HTTP 400 *while the server still wrote the voucher row* —
a server-side double-write.

What the tests below establish, against the current code + runtime:

1. **No double-write is possible on this route.** ``POST /vouchers/``
   declares ``data: VoucherCreate``, so FastAPI parses+validates the body
   during parameter resolution, before the handler body runs. When the
   body is un-parseable the request is rejected and neither the
   idempotency claim (``idem.check``) nor ``service.create`` executes —
   ``test_malformed_body_4xx_writes_nothing`` proves zero rows land.

2. **A BOM body does not even 400 here — it is silently accepted.**
   Python's ``json.loads`` auto-detects the BOM and decodes it, so a
   BOM-prefixed body creates exactly one voucher (201). The May-2026
   "error parsing the body" 400 was environment/Python-version specific;
   it is not reproducible on the current runtime and, either way, cannot
   double-write (see #1).

3. **The genuine duplicate mechanism is client-side.** A client that
   treats a slow/odd response as a failure and retries with a *fresh*
   Idempotency-Key creates a second voucher
   (``test_retry_with_new_key_creates_duplicate``). Reusing the key is
   safe (``test_retry_reusing_key_is_idempotent``). This is the contract
   §7.5b checkbox 6 must exercise, and the corrected root cause of
   BUG-001: it is client key-discipline, not a server defect.

These tests pin the invariants so a future regression — e.g. switching
to a manual ``await request.json()`` that writes before validating —
is caught.
"""

from __future__ import annotations

import json
from uuid import uuid4

from app.models.idempotency_key import IdempotencyKey
from app.models.voucher import Voucher
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.integration.api.test_vouchers_create import _h, _payload, _setup


def test_bom_prefixed_body_is_accepted_creates_exactly_one(
    client: TestClient, db_session: Session
) -> None:
    """A UTF-16-BOM body (the PowerShell here-string shape) is decoded and
    creates exactly one voucher — it does NOT 400 and does NOT duplicate.

    This is the real trap behind BUG-001: a request an operator may read
    as "failed" actually succeeded server-side.
    """
    user, company, bank, party = _setup(db_session)
    key = str(uuid4())
    raw = json.dumps(_payload(bank, party)).encode("utf-16")  # BOM-prefixed

    r = client.post(
        "/api/v1/vouchers/",
        headers={
            **_h(user, company, idem=key),
            "Content-Type": "application/json",
        },
        content=raw,
    )

    assert r.status_code == 201, r.text
    db_session.expire_all()
    assert db_session.query(Voucher).count() == 1
    assert db_session.query(IdempotencyKey).filter_by(key=key).count() == 1


def test_malformed_body_4xx_writes_nothing(
    client: TestClient, db_session: Session
) -> None:
    """A genuinely un-parseable body is rejected and leaves the DB untouched.

    Disproves BUG-001 candidate 1 for *any* body malformation: when the
    body fails to parse, the handler never runs, so no voucher row and no
    idempotency claim row is written.
    """
    user, company, bank, party = _setup(db_session)
    key = str(uuid4())

    r = client.post(
        "/api/v1/vouchers/",
        headers={
            **_h(user, company, idem=key),
            "Content-Type": "application/json",
        },
        content=b'{"voucher_type": "Receipt", NOT VALID JSON',
    )

    assert r.status_code != 201
    assert 400 <= r.status_code < 500, r.text
    db_session.expire_all()
    assert db_session.query(Voucher).count() == 0
    assert db_session.query(IdempotencyKey).filter_by(key=key).count() == 0


def test_retry_reusing_key_is_idempotent(
    client: TestClient, db_session: Session
) -> None:
    """Retrying with the SAME Idempotency-Key replays — exactly one voucher.

    The correct client behavior that prevents the BUG-001 duplicate.
    """
    user, company, bank, party = _setup(db_session)
    key = str(uuid4())
    payload = _payload(bank, party)

    r1 = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=key),
        json=payload,
    )
    assert r1.status_code == 201, r1.text

    r2 = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=key),
        json=payload,
    )
    assert r2.status_code == 201
    assert r2.json()["id"] == r1.json()["id"]
    assert r2.headers.get("Idempotent-Replay") == "true"

    db_session.expire_all()
    assert db_session.query(Voucher).count() == 1


def test_retry_with_new_key_creates_duplicate(
    client: TestClient, db_session: Session
) -> None:
    """The ONLY way an identical POST duplicates is a fresh key.

    This is BUG-001's real mechanism: idempotency protects a retry only
    when the client reuses the key. A new key is a new logical request,
    so two vouchers result. Documents the client-side contract that
    §7.5b checkbox 6 must exercise.
    """
    user, company, bank, party = _setup(db_session)
    payload = _payload(bank, party)

    r1 = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=str(uuid4())),
        json=payload,
    )
    assert r1.status_code == 201

    r2 = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=str(uuid4())),  # NEW key
        json=payload,
    )
    assert r2.status_code == 201
    assert r2.json()["id"] != r1.json()["id"]

    db_session.expire_all()
    assert db_session.query(Voucher).count() == 2

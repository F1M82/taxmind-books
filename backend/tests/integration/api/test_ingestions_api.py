"""Integration tests for POST/GET /api/v1/ingestions/.

Default conftest env has TAXMIND_SKIP_TALLY_DISPATCH=1, which
`enqueue_extraction` reuses (see extraction_pipeline.py docstring) --
no Gemini call ever happens in these tests, same as the voucher tests
never reach a live Tally connector.
"""

from __future__ import annotations

import io

from app.models.company import CompanyRole
from app.models.ingestion import Ingestion, IngestionStatus
from app.models.voucher import Voucher, VoucherStatus
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from tests._db_fixtures import issue_token, make_company, make_membership, make_user


def _headers(user, company, *, idem: str) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
        "Idempotency-Key": idem,
    }


def _jpeg_file() -> bytes:
    img = Image.new("RGB", (50, 40), color="yellow")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _setup(db_session: Session):  # type: ignore[no-untyped-def]
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    return user, company


def test_upload_creates_ingestion_and_enqueues(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    resp = client.post(
        "/api/v1/ingestions/",
        headers=_headers(user, company, idem="upload-key-001"),
        files={"file": ("invoice.jpg", _jpeg_file(), "image/jpeg")},
        data={"source": "photo"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["source"] == "photo"
    assert body["content_type"] == "image/jpeg"
    assert body["status"] in ("received", "extracting", "extracted")

    row = db_session.get(Ingestion, body["id"])
    assert row is not None
    assert row.company_id == company.id


def test_idempotency_replay_returns_same_ingestion(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    headers = _headers(user, company, idem="upload-key-002")
    files = {"file": ("invoice.jpg", _jpeg_file(), "image/jpeg")}

    first = client.post(
        "/api/v1/ingestions/", headers=headers, files=files, data={"source": "photo"}
    )
    second = client.post(
        "/api/v1/ingestions/", headers=headers, files=files, data={"source": "photo"}
    )
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]

    count = (
        db_session.query(Ingestion)
        .filter(Ingestion.company_id == company.id)
        .count()
    )
    assert count == 1


def test_unsupported_content_type_rejected(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    resp = client.post(
        "/api/v1/ingestions/",
        headers=_headers(user, company, idem="upload-key-003"),
        files={"file": ("doc.txt", b"hello", "text/plain")},
        data={"source": "photo"},
    )
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "unsupported_media_type"


def test_oversized_file_rejected(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    big = b"\x00" * (10 * 1024 * 1024 + 1)
    resp = client.post(
        "/api/v1/ingestions/",
        headers=_headers(user, company, idem="upload-key-004"),
        files={"file": ("invoice.jpg", big, "image/jpeg")},
        data={"source": "photo"},
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "file_too_large"


def test_get_by_id_and_list(client: TestClient, db_session: Session) -> None:
    user, company = _setup(db_session)
    created = client.post(
        "/api/v1/ingestions/",
        headers=_headers(user, company, idem="upload-key-005"),
        files={"file": ("invoice.jpg", _jpeg_file(), "image/jpeg")},
        data={"source": "photo"},
    ).json()

    get_resp = client.get(
        f"/api/v1/ingestions/{created['id']}",
        headers={
            "Authorization": f"Bearer {issue_token(user)}",
            "X-Company-ID": str(company.id),
        },
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == created["id"]

    list_resp = client.get(
        "/api/v1/ingestions/",
        headers={
            "Authorization": f"Bearer {issue_token(user)}",
            "X-Company-ID": str(company.id),
        },
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["meta"]["total"] == 1


def test_get_unknown_ingestion_404s(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    resp = client.get(
        "/api/v1/ingestions/00000000-0000-0000-0000-000000000000",
        headers={
            "Authorization": f"Bearer {issue_token(user)}",
            "X-Company-ID": str(company.id),
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ingestion_not_found"


# ---------------- Mobile review queue: discard + link-voucher ----------------


def _make_ingestion(
    db_session: Session, company, *, status_=IngestionStatus.review
):  # type: ignore[no-untyped-def]
    ing = Ingestion(
        company_id=company.id,
        source="photo",
        status=status_,
        content_type="image/jpeg",
        content_size=100,
    )
    db_session.add(ing)
    db_session.commit()
    return ing


def _make_voucher(db_session: Session, company):  # type: ignore[no-untyped-def]
    from datetime import date
    from decimal import Decimal

    v = Voucher(
        company_id=company.id,
        voucher_type="Purchase",
        date=date(2026, 5, 8),
        total_amount=Decimal("1000.00"),
        status=VoucherStatus.pending_tally_post,
        source="manual",
        is_auto_posted=False,
        gst_applicable=False,
    )
    db_session.add(v)
    db_session.commit()
    return v


def test_discard_review_ingestion(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    ing = _make_ingestion(db_session, company)
    resp = client.post(
        f"/api/v1/ingestions/{ing.id}/discard",
        headers={
            "Authorization": f"Bearer {issue_token(user)}",
            "X-Company-ID": str(company.id),
        },
        json={"reason": "duplicate scan"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "discarded"
    db_session.refresh(ing)
    assert ing.status == IngestionStatus.discarded


def test_discard_already_resolved_ingestion_409s(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    ing = _make_ingestion(db_session, company, status_=IngestionStatus.posted)
    resp = client.post(
        f"/api/v1/ingestions/{ing.id}/discard",
        headers={
            "Authorization": f"Bearer {issue_token(user)}",
            "X-Company-ID": str(company.id),
        },
        json={},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ingestion_already_resolved"


def test_link_voucher_resolves_ingestion(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    ing = _make_ingestion(db_session, company, status_=IngestionStatus.matched)
    voucher = _make_voucher(db_session, company)
    resp = client.post(
        f"/api/v1/ingestions/{ing.id}/link-voucher",
        headers={
            "Authorization": f"Bearer {issue_token(user)}",
            "X-Company-ID": str(company.id),
        },
        json={"voucher_id": str(voucher.id)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "posted"
    assert body["posted_voucher_id"] == str(voucher.id)


def test_link_voucher_from_other_company_404s(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    other_company = make_company(db_session)
    ing = _make_ingestion(db_session, company, status_=IngestionStatus.review)
    foreign_voucher = _make_voucher(db_session, other_company)
    resp = client.post(
        f"/api/v1/ingestions/{ing.id}/link-voucher",
        headers={
            "Authorization": f"Bearer {issue_token(user)}",
            "X-Company-ID": str(company.id),
        },
        json={"voucher_id": str(foreign_voucher.id)},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "voucher_not_found"


def test_link_voucher_already_posted_409s(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    ing = _make_ingestion(db_session, company, status_=IngestionStatus.posted)
    voucher = _make_voucher(db_session, company)
    resp = client.post(
        f"/api/v1/ingestions/{ing.id}/link-voucher",
        headers={
            "Authorization": f"Bearer {issue_token(user)}",
            "X-Company-ID": str(company.id),
        },
        json={"voucher_id": str(voucher.id)},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ingestion_already_resolved"

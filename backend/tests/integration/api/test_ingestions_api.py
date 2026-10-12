"""Integration tests for POST/GET /api/v1/ingestions/.

Default conftest env has TAXMIND_SKIP_TALLY_DISPATCH=1, which
`enqueue_extraction` reuses (see extraction_pipeline.py docstring) --
no Gemini call ever happens in these tests, same as the voucher tests
never reach a live Tally connector.
"""

from __future__ import annotations

import io

from app.models.company import CompanyRole
from app.models.ingestion import Ingestion
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

"""Integration tests for notification_service.send_to_user (P0.44).

The service routes per-platform (android/web → FCM, ios → APNs).
Tests monkey-patch the integrations modules — not the symbols — so
the dispatch picks up the patches; see CONNECTOR_PROTOCOL.md
§"Patchable singletons" for the rule.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.integrations import apns_client as apns_mod
from app.integrations import fcm_client as fcm_mod
from app.models.device_token import DevicePlatform, DeviceToken
from app.services.notification_service import (
    Notification,
    send_to_user,
)
from sqlalchemy.orm import Session

from tests._db_fixtures import make_user


def _device(  # type: ignore[no-untyped-def]
    db: Session,
    user_id,
    *,
    token: str,
    platform: DevicePlatform,
    active: bool = True,
) -> DeviceToken:
    row = DeviceToken(
        user_id=user_id,
        token=token,
        platform=platform,
        app_version="1.0.0",
        is_active=active,
        last_active_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def test_routes_android_to_fcm_and_ios_to_apns(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(db_session)
    _device(
        db_session, user.id, token="and-1", platform=DevicePlatform.android
    )
    _device(db_session, user.id, token="ios-1", platform=DevicePlatform.ios)
    _device(db_session, user.id, token="web-1", platform=DevicePlatform.web)
    db_session.commit()

    fcm_calls: list[dict[str, object]] = []
    apns_calls: list[dict[str, object]] = []

    def _fake_fcm(**kwargs: object) -> fcm_mod.FcmResult:
        fcm_calls.append(kwargs)
        return fcm_mod.FcmResult(delivered=True)

    def _fake_apns(**kwargs: object) -> apns_mod.ApnsResult:
        apns_calls.append(kwargs)
        return apns_mod.ApnsResult(delivered=True)

    monkeypatch.setattr(fcm_mod, "send_push", _fake_fcm)
    monkeypatch.setattr(apns_mod, "send_push", _fake_apns)

    results = send_to_user(
        db_session,
        user_id=user.id,
        notification=Notification(title="Hello", body="World"),
    )

    assert len(results) == 3
    assert all(r.delivered for r in results)

    fcm_tokens = sorted(c["token"] for c in fcm_calls)
    assert fcm_tokens == ["and-1", "web-1"]
    apns_tokens = [c["token"] for c in apns_calls]
    assert apns_tokens == ["ios-1"]


def test_skips_inactive_tokens(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(db_session)
    _device(
        db_session,
        user.id,
        token="active",
        platform=DevicePlatform.android,
        active=True,
    )
    _device(
        db_session,
        user.id,
        token="inactive",
        platform=DevicePlatform.android,
        active=False,
    )
    db_session.commit()

    fcm_calls: list[dict[str, object]] = []

    def _fake_fcm(**kwargs: object) -> fcm_mod.FcmResult:
        fcm_calls.append(kwargs)
        return fcm_mod.FcmResult(delivered=True)

    monkeypatch.setattr(fcm_mod, "send_push", _fake_fcm)
    monkeypatch.setattr(
        apns_mod,
        "send_push",
        lambda **_: apns_mod.ApnsResult(delivered=True),
    )

    results = send_to_user(
        db_session,
        user_id=user.id,
        notification=Notification(title="X", body="Y"),
    )

    assert len(results) == 1
    assert [c["token"] for c in fcm_calls] == ["active"]


def test_returns_empty_when_user_has_no_devices(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(db_session)

    monkeypatch.setattr(
        fcm_mod,
        "send_push",
        lambda **_: pytest.fail("FCM must not be called"),
    )
    monkeypatch.setattr(
        apns_mod,
        "send_push",
        lambda **_: pytest.fail("APNs must not be called"),
    )

    results = send_to_user(
        db_session,
        user_id=user.id,
        notification=Notification(title="X", body="Y"),
    )
    assert results == []


def test_only_dispatches_to_the_target_user(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_a = make_user(db_session, email="a@example.com")
    user_b = make_user(db_session, email="b@example.com")
    _device(
        db_session,
        user_a.id,
        token="a-token",
        platform=DevicePlatform.android,
    )
    _device(
        db_session,
        user_b.id,
        token="b-token",
        platform=DevicePlatform.android,
    )
    db_session.commit()

    seen: list[str] = []
    monkeypatch.setattr(
        fcm_mod,
        "send_push",
        lambda **kwargs: (
            seen.append(str(kwargs["token"])),
            fcm_mod.FcmResult(delivered=True),
        )[1],
    )
    monkeypatch.setattr(
        apns_mod,
        "send_push",
        lambda **_: apns_mod.ApnsResult(delivered=True),
    )

    send_to_user(
        db_session,
        user_id=user_a.id,
        notification=Notification(title="X", body="Y"),
    )
    assert seen == ["a-token"]


def test_propagates_provider_error(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(db_session)
    _device(
        db_session,
        user.id,
        token="bad",
        platform=DevicePlatform.android,
    )
    db_session.commit()

    monkeypatch.setattr(
        fcm_mod,
        "send_push",
        lambda **_: fcm_mod.FcmResult(
            delivered=False, error="invalid_registration"
        ),
    )
    monkeypatch.setattr(
        apns_mod,
        "send_push",
        lambda **_: apns_mod.ApnsResult(delivered=True),
    )

    results = send_to_user(
        db_session,
        user_id=user.id,
        notification=Notification(title="X", body="Y"),
    )
    assert len(results) == 1
    assert results[0].delivered is False
    assert results[0].error == "invalid_registration"

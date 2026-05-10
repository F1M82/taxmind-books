"""Auth service — registration, login, refresh, me, password change."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.exceptions import (
    EmailAlreadyRegistered,
    InvalidCredentials,
    UserInactive,
)
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import RegisterRequest


def _user_snapshot(user: User) -> dict[str, object | None]:
    """Audit snapshot — never includes hashed_password / phone."""
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "is_ca": user.is_ca,
        "firm_name": user.firm_name,
        "ca_membership_no": user.ca_membership_no,
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
    }


class AuthService:
    def __init__(self, db: Session, audit: AuditEmitter) -> None:
        self.db = db
        self.audit = audit

    # ------------------------------------------------------------------
    # register
    # ------------------------------------------------------------------

    def register(self, data: RegisterRequest) -> User:
        email = data.email.lower()

        existing = self.db.query(User).filter(User.email == email).first()
        if existing is not None:
            raise EmailAlreadyRegistered(
                "Email already registered.",
                details={"email": email},
            )

        user = User(
            email=email,
            hashed_password=hash_password(data.password),
            full_name=data.full_name,
            phone=data.phone,
            is_ca=data.is_ca,
            firm_name=data.firm_name,
            ca_membership_no=data.ca_membership_no,
            is_active=True,
            is_superuser=False,
        )
        self.db.add(user)
        self.db.flush()

        # Self-registration: the new user is the actor.
        self.audit.emit(
            action="user.created",
            entity_type="user",
            entity_id=user.id,
            old_value=None,
            new_value=_user_snapshot(user),
            actor_user_id=user.id,
        )
        return user

    # ------------------------------------------------------------------
    # login
    # ------------------------------------------------------------------

    def authenticate(self, email: str, password: str) -> User:
        """Verify credentials and stamp last_login_at.

        Per API.md: do not distinguish "user not found" from "wrong
        password" — both surface as `401 invalid_credentials`. Only
        an inactive user gets the distinct `403 user_inactive` (after
        a correct password match).
        """
        user = self.db.query(User).filter(User.email == email.lower()).first()
        if user is None or not verify_password(password, user.hashed_password):
            raise InvalidCredentials("Invalid email or password.")
        if not user.is_active:
            raise UserInactive("User account is inactive.")
        user.last_login_at = datetime.now(UTC)
        self.db.flush()
        return user

    # ------------------------------------------------------------------
    # refresh
    # ------------------------------------------------------------------

    def refresh(self, refresh_token: str) -> User:
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except TokenError as exc:
            raise InvalidCredentials(
                "Invalid or expired refresh token.",
                details={"code": "invalid_refresh_token"},
            ) from exc
        try:
            user_id = UUID(payload.sub)
        except (ValueError, TypeError) as exc:
            raise InvalidCredentials(
                "Invalid refresh token.",
                details={"code": "invalid_refresh_token"},
            ) from exc
        user = self.db.query(User).filter(User.id == user_id).first()
        if user is None or not user.is_active:
            raise InvalidCredentials(
                "Invalid refresh token.",
                details={"code": "invalid_refresh_token"},
            )
        return user

    # ------------------------------------------------------------------
    # change password
    # ------------------------------------------------------------------

    def change_password(
        self, user: User, current_password: str, new_password: str
    ) -> None:
        if not verify_password(current_password, user.hashed_password):
            raise InvalidCredentials("Current password is incorrect.")
        user.hashed_password = hash_password(new_password)
        self.db.flush()
        # `user.password_changed` is a system event — no company scope.
        # The AuditEmitter ctx may have company set (when invoked from
        # an authenticated request), but the password change is not
        # tenant-relevant; we override with no company context by
        # writing the row directly via emit() and relying on the audit
        # column's nullability + the passed actor_user_id.
        self.audit.emit(
            action="user.password_changed",
            entity_type="user",
            entity_id=user.id,
            old_value=None,
            new_value={"id": str(user.id), "email": user.email},
            actor_user_id=user.id,
        )


# ---------------------------------------------------------------------
# Token helpers (no service state needed)
# ---------------------------------------------------------------------


def issue_token_pair(user: User) -> tuple[str, str]:
    return create_access_token(user.id), create_refresh_token(user.id)

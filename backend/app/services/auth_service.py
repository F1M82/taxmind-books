"""Auth service — registration (P0.14).

Login / refresh / me / password change land in P0.15 here.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.exceptions import EmailAlreadyRegistered
from app.core.security import hash_password
from app.models.user import User
from app.schemas.auth import RegisterRequest


def _user_snapshot(user: User) -> dict[str, object]:
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
    """Service for user-authentication flows.

    Constructor takes only the bare DB + audit dependencies; it does
    not need a `Company` because user lifecycle is global (see
    AUDIT.md §"Tenant-scoped vs system events").
    """

    def __init__(self, db: Session, audit: AuditEmitter) -> None:
        self.db = db
        self.audit = audit

    def register(self, data: RegisterRequest) -> User:
        """Create a new user. Email is lowercased + uniqueness-checked."""
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
        self.db.flush()  # populate user.id for the audit row

        # Self-registration: the new user is the actor (user_id) AND
        # the entity. There's no logged-in caller, so we override
        # `actor_user_id` explicitly rather than relying on ctx.user.
        self.audit.emit(
            action="user.created",
            entity_type="user",
            entity_id=user.id,
            old_value=None,
            new_value=_user_snapshot(user),
            actor_user_id=user.id,
        )
        return user

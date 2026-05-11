"""Company service: CRUD + member management (P0.16)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.exceptions import (
    AlreadyMember,
    CompanyNotFound,
    GstinAlreadyRegistered,
    UserNotFound,
)
from app.models.company import Company, CompanyRole, CompanyStatus, UserCompany
from app.models.user import User
from app.schemas.company import CompanyCreate, CompanyUpdate


def _company_snapshot(c: Company) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "name": c.name,
        "gstin": c.gstin,
        "pan": c.pan,
        "financial_year_start": c.financial_year_start.isoformat(),
        "status": c.status.value if hasattr(c.status, "value") else str(c.status),
        "address": c.address,
        "city": c.city,
        "state_code": c.state_code,
        "pincode": c.pincode,
        "accounting_source": c.accounting_source,
    }


class CompanyService:
    """All company mutations land through this service.

    Companies are the *tenant root* — they're not themselves tenant-
    scoped. Construction takes only db + audit; the active user (the
    actor) is passed per method.
    """

    def __init__(self, db: Session, audit: AuditEmitter) -> None:
        self.db = db
        self.audit = audit

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, data: CompanyCreate, actor: User) -> tuple[Company, UserCompany]:
        if data.gstin is not None:
            existing = (
                self.db.query(Company).filter(Company.gstin == data.gstin).first()
            )
            if existing is not None:
                raise GstinAlreadyRegistered(
                    "GSTIN already registered.",
                    details={"gstin": data.gstin},
                )

        kwargs: dict[str, Any] = {
            "name": data.name,
            "gstin": data.gstin,
            "pan": data.pan,
            "address": data.address,
            "city": data.city,
            "state_code": data.state_code,
            "pincode": data.pincode,
            "accounting_source": data.accounting_source,
            "created_by": actor.id,
        }
        if data.financial_year_start is not None:
            kwargs["financial_year_start"] = data.financial_year_start
        company = Company(**kwargs)
        self.db.add(company)
        self.db.flush()  # populate company.id

        # Caller becomes owner (per API.md).
        membership = UserCompany(
            user_id=actor.id,
            company_id=company.id,
            role=CompanyRole.owner,
        )
        self.db.add(membership)
        self.db.flush()

        self.audit.emit(
            action="company.created",
            entity_type="company",
            entity_id=company.id,
            old_value=None,
            new_value=_company_snapshot(company),
            company_id_override=company.id,
        )
        return company, membership

    def get(self, company_id: UUID, actor: User) -> Company:
        membership = self._require_membership(company_id, actor)
        company = (
            self.db.query(Company).filter(Company.id == company_id).first()
        )
        if company is None or company.status != CompanyStatus.active:
            raise CompanyNotFound("Company not found.")
        # Returning company; route uses it together with membership for `your_role`.
        # Cache the role on the object so the caller doesn't re-query.
        company._cached_role = membership.role.value  # type: ignore[attr-defined]
        return company

    def update(
        self, company_id: UUID, data: CompanyUpdate, actor: User
    ) -> Company:
        membership = self._require_membership(company_id, actor)
        if membership.role not in (CompanyRole.owner, CompanyRole.admin):
            from app.core.exceptions import InsufficientRole

            raise InsufficientRole("Insufficient role.")
        company = (
            self.db.query(Company).filter(Company.id == company_id).one()
        )
        old = _company_snapshot(company)

        # Apply only provided fields (model_dump exclude_unset).
        diff = data.model_dump(exclude_unset=True)
        if "gstin" in diff and diff["gstin"] != company.gstin and diff["gstin"] is not None:
            exists = (
                self.db.query(Company)
                .filter(
                    Company.gstin == diff["gstin"],
                    Company.id != company.id,
                )
                .first()
            )
            if exists is not None:
                raise GstinAlreadyRegistered(
                    "GSTIN already registered.",
                    details={"gstin": diff["gstin"]},
                )
        for k, v in diff.items():
            setattr(company, k, v)
        self.db.flush()
        new = _company_snapshot(company)

        self.audit.emit(
            action="company.settings_updated",
            entity_type="company",
            entity_id=company.id,
            old_value=old,
            new_value=new,
            company_id_override=company.id,
        )
        company._cached_role = membership.role.value  # type: ignore[attr-defined]
        return company

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_for_user(
        self, actor: User, *, limit: int, cursor: str | None
    ) -> tuple[list[tuple[Company, str]], str | None, int]:
        """Return (rows, next_cursor, total) for the active user.

        Phase-0 cursor: an opaque base64 of `created_at|id` from the
        last item; null when the page is the final one.
        """
        from base64 import urlsafe_b64decode, urlsafe_b64encode

        q = (
            self.db.query(Company, UserCompany.role)
            .join(UserCompany, UserCompany.company_id == Company.id)
            .filter(UserCompany.user_id == actor.id)
            .filter(Company.status == CompanyStatus.active)
            .order_by(Company.created_at.desc(), Company.id.desc())
        )
        total = q.count()
        if cursor:
            try:
                decoded = urlsafe_b64decode(cursor.encode()).decode()
                ts_str, id_str = decoded.split("|", 1)
                from datetime import datetime as _dt
                from uuid import UUID as _UUID

                ts = _dt.fromisoformat(ts_str)
                last_id = _UUID(id_str)
                q = q.filter(
                    (Company.created_at < ts)
                    | (
                        (Company.created_at == ts)
                        & (Company.id < last_id)
                    )
                )
            except (ValueError, TypeError):
                # Bad cursor → treat as start. Don't 422 — clients
                # roll cursors forward and a stale cursor on a deleted
                # row would otherwise lock them out.
                pass
        rows = q.limit(limit).all()
        next_cursor: str | None = None
        if len(rows) == limit and total > limit:
            last_company, _ = rows[-1]
            payload = (
                f"{last_company.created_at.isoformat()}|{last_company.id}"
            )
            next_cursor = urlsafe_b64encode(payload.encode()).decode()
        return [(c, role.value) for (c, role) in rows], next_cursor, total

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    def add_member(
        self,
        company_id: UUID,
        email: str,
        role: str,
        actor: User,
    ) -> UserCompany:
        membership = self._require_membership(company_id, actor)
        if membership.role != CompanyRole.owner:
            from app.core.exceptions import InsufficientRole

            raise InsufficientRole("Insufficient role.")

        target = (
            self.db.query(User).filter(User.email == email.lower()).first()
        )
        if target is None:
            raise UserNotFound(
                "User not found. The invitee must register before being added.",
                details={"email": email.lower()},
            )

        existing = (
            self.db.query(UserCompany)
            .filter(
                UserCompany.user_id == target.id,
                UserCompany.company_id == company_id,
            )
            .first()
        )
        if existing is not None:
            raise AlreadyMember(
                "User is already a member of this company.",
                details={
                    "user_id": str(target.id),
                    "current_role": existing.role.value,
                },
            )

        new_membership = UserCompany(
            user_id=target.id,
            company_id=company_id,
            role=CompanyRole(role),
        )
        self.db.add(new_membership)
        self.db.flush()

        self.audit.emit(
            action="user_company.role_assigned",
            entity_type="user_company",
            entity_id=new_membership.id,
            old_value=None,
            new_value={
                "user_id": str(target.id),
                "user_email": target.email,
                "company_id": str(company_id),
                "role": role,
            },
            company_id_override=company_id,
        )
        return new_membership

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_membership(
        self, company_id: UUID, actor: User
    ) -> UserCompany:
        membership = (
            self.db.query(UserCompany)
            .filter(
                UserCompany.user_id == actor.id,
                UserCompany.company_id == company_id,
            )
            .first()
        )
        if membership is None:
            raise CompanyNotFound("Company not found.")
        return membership

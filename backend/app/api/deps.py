"""FastAPI dependencies for auth + tenant scoping.

Implements the three-layer chain from `docs/TENANCY.md`:

  1. `get_current_user`   — decode JWT, load user, check is_active
  2. `get_active_company` — resolve X-Company-ID header against the
                            user's memberships; 404 on miss to prevent
                            enumeration
  3. `get_scoped_session` — yields a Session that auto-injects a
                            `WHERE company_id = <active>` filter on
                            every SELECT / UPDATE / DELETE against
                            `TenantScopedMixin` models

Plus the `require_role(*roles)` factory for role-restricted endpoints.

This module is the single source of tenant identity in the request
path. No route handler ever reads `company_id` from the request body
or query string. No service ever accepts `company_id` as a parameter.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, with_loader_criteria

from app.core.database import SCOPE_BYPASS_OPTION, SessionLocal, get_db
from app.core.exceptions import CompanyNotFound, InsufficientRole
from app.core.security import (
    ACCESS_TOKEN_TYPE,
    TokenError,
    decode_token,
)
from app.models.base import TenantScopedMixin
from app.models.company import Company, CompanyRole, CompanyStatus, UserCompany
from app.models.user import User

__all__ = [
    "SCOPE_BYPASS_OPTION",
    "get_active_company",
    "get_active_membership",
    "get_current_user",
    "get_idempotency_handler",
    "get_scoped_session",
    "oauth2_scheme",
    "require_role",
]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# ---------------------------------------------------------------------
# 1. Current user
# ---------------------------------------------------------------------


def _credentials_exc() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Decode the bearer token and return the active `User`.

    Rejects (401) on any decode failure, missing user, or inactive user.
    """
    try:
        payload = decode_token(token, expected_type=ACCESS_TOKEN_TYPE)
    except TokenError as exc:
        raise _credentials_exc() from exc

    try:
        user_id = UUID(payload.sub)
    except (ValueError, TypeError) as exc:
        raise _credentials_exc() from exc

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise _credentials_exc()
    return user


# ---------------------------------------------------------------------
# 2. Active company
# ---------------------------------------------------------------------


def get_active_company(
    x_company_id: UUID = Header(..., alias="X-Company-ID"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Company:
    """Resolve the active `Company` from the `X-Company-ID` header.

    Returns 404 (not 403) when the user lacks membership or the
    company is suspended/missing — by design, to prevent existence
    enumeration of company IDs the user cannot see.
    """
    membership = (
        db.query(UserCompany)
        .filter(
            UserCompany.user_id == user.id,
            UserCompany.company_id == x_company_id,
        )
        .first()
    )
    if membership is None:
        raise CompanyNotFound("Company not found.")

    company = db.query(Company).filter(Company.id == x_company_id).first()
    if company is None or company.status != CompanyStatus.active:
        raise CompanyNotFound("Company not found.")

    return company


def get_active_membership(
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserCompany:
    """Return the `UserCompany` row for (user, active_company).

    Used by `require_role` and any endpoint that needs the role.
    Always succeeds when `get_active_company` did, by construction.
    """
    membership = (
        db.query(UserCompany)
        .filter(
            UserCompany.user_id == user.id,
            UserCompany.company_id == company.id,
        )
        .first()
    )
    # Defensive: get_active_company already verified membership.
    if membership is None:  # pragma: no cover
        raise CompanyNotFound("Company not found.")
    return membership


# ---------------------------------------------------------------------
# 3. require_role factory
# ---------------------------------------------------------------------


def require_role(
    *roles: CompanyRole | str,
) -> Callable[[UserCompany], Company]:
    """Return a dependency that 403s unless the user's role is in `roles`.

    Usage::

        @router.get(..., dependencies=[Depends(require_role("owner", "admin"))])

    Or as a value dependency to also receive the `Company`::

        company: Company = Depends(require_role("owner"))
    """
    allowed: set[str] = {
        r.value if isinstance(r, CompanyRole) else r for r in roles
    }

    def checker(
        membership: UserCompany = Depends(get_active_membership),
        company: Company = Depends(get_active_company),
    ) -> Company:
        if membership.role.value not in allowed:
            raise InsufficientRole("Insufficient role.")
        return company

    return checker


# ---------------------------------------------------------------------
# 4. Scoped session
# ---------------------------------------------------------------------


def get_scoped_session(
    company: Company = Depends(get_active_company),
) -> Generator[Session, None, None]:
    """Yield a `Session` that auto-scopes queries on `TenantScopedMixin`.

    The session intercepts every ORM SELECT / UPDATE / DELETE and
    injects ``WHERE company_id = <active>`` via `with_loader_criteria`.
    INSERT is intentionally *not* scoped — callers must set
    `company_id` explicitly so a missing assignment is a hard error,
    not a silent overwrite.

    To run a one-off cross-tenant query (admin tools, system tasks),
    call ``execution_options(skip_tenant_scope=True)`` on the
    statement before executing.
    """
    from sqlalchemy import event

    db = SessionLocal()
    company_id = company.id

    @event.listens_for(db, "do_orm_execute")
    def _scope_query(execute_state):  # type: ignore[no-untyped-def]
        if execute_state.execution_options.get(SCOPE_BYPASS_OPTION):
            return
        if not (
            execute_state.is_select
            or execute_state.is_update
            or execute_state.is_delete
        ):
            return
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                TenantScopedMixin,
                lambda cls: cls.company_id == company_id,
                include_aliases=True,
            )
        )

    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------
# 5. Idempotency handler
# ---------------------------------------------------------------------


def get_idempotency_handler(
    request: Request,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
):  # type: ignore[no-untyped-def]
    """Per-request `IdempotencyHandler` bound to (active company, user, request)."""
    from app.core.idempotency import IdempotencyHandler

    return IdempotencyHandler(
        request=request,
        db=db,
        company_id=company.id,
        user_id=user.id,
    )

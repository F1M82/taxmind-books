"""Onboarding endpoint (P0.42) — `GET /api/v1/onboarding/checklist`.

Read-only. Any company member can call it (per the R9 read-access
convention used by the reports endpoints). No idempotency header,
no audit emission.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import (
    get_active_company,
    get_current_user,
    get_scoped_session,
)
from app.models.company import Company
from app.models.user import User
from app.schemas.onboarding import (
    OnboardingChecklistResponse,
    OnboardingItem,
)
from app.services.onboarding_service import build_checklist

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get(
    "/checklist",
    response_model=OnboardingChecklistResponse,
    response_model_exclude_none=True,
)
def get_checklist(
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> OnboardingChecklistResponse:
    data = build_checklist(db, company=company)
    return OnboardingChecklistResponse(
        company_id=company.id,
        items=[
            OnboardingItem(
                key=i.key,  # type: ignore[arg-type]
                label=i.label,
                completed=i.completed,
                completed_at=i.completed_at,
            )
            for i in data.items
        ],
        completed_count=data.completed_count,
        total_count=data.total_count,
    )

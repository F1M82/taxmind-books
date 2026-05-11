"""Top-level v1 router.

Subrouters for each resource live in this package. Register new
routers here so the OpenAPI spec at `/openapi.json` stays exhaustive.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    audit_logs,
    auth,
    companies,
    connector,
    ledgers,
    vouchers,
)

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth.router)
api_v1.include_router(companies.router)
api_v1.include_router(ledgers.router)
api_v1.include_router(vouchers.router)
api_v1.include_router(audit_logs.router)
api_v1.include_router(connector.router)

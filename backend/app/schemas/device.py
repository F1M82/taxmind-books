"""Device-token schemas (P0.44).

Shapes mirror docs/API.md §"Devices & Push Notifications".
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import StringConstraints

from app.schemas.common import TaxMindBooksBase

DevicePlatformStr = Literal["android", "ios", "web"]


class DeviceRegisterRequest(TaxMindBooksBase):
    token: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    platform: DevicePlatformStr
    app_version: Annotated[str, StringConstraints(max_length=50)] | None = None


class DeviceRegisterResponse(TaxMindBooksBase):
    id: UUID
    token_registered: bool

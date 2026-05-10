"""Unit tests for app.api.deps — pure-shape checks.

End-to-end behavior is covered by tests/integration/test_tenancy_dependencies.
This module verifies the small surface that doesn't need a live DB:
the `require_role` factory and module-level OAuth2 scheme config.
"""

from __future__ import annotations

from app.api import deps
from app.models.company import CompanyRole


def test_oauth2_scheme_token_url() -> None:
    """The bearer scheme points at the v1 login endpoint."""
    assert deps.oauth2_scheme.model.flows.password.tokenUrl.endswith(  # type: ignore[attr-defined]
        "/api/v1/auth/login"
    )


def test_require_role_returns_callable() -> None:
    dep = deps.require_role(CompanyRole.owner, CompanyRole.admin)
    assert callable(dep)


def test_require_role_accepts_str_or_enum() -> None:
    """Both `CompanyRole.owner` and `'owner'` should be accepted."""
    a = deps.require_role(CompanyRole.owner)
    b = deps.require_role("owner")
    assert callable(a) and callable(b)


def test_scope_bypass_option_is_string() -> None:
    """The execution_options key is the documented string."""
    assert deps.SCOPE_BYPASS_OPTION == "skip_tenant_scope"

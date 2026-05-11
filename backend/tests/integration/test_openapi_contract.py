"""OpenAPI contract test (P0.34).

Diffs the live FastAPI `/openapi.json` against the Phase-0 reference
in `openapi_phase0.yaml`. Build fails when:

  - A path/method exists in the spec but isn't declared in the
    reference (accidentally exposed surface).
  - A path/method in the reference is missing from the spec
    (route deleted or renamed without updating docs/API.md).

The reference tracks path+method only — see the rationale at the
top of the YAML.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from app.main import create_app
from fastapi.testclient import TestClient

_REFERENCE = Path(__file__).resolve().parent / "openapi_phase0.yaml"


@pytest.fixture(scope="module")
def reference() -> dict[str, set[str]]:
    raw = yaml.safe_load(_REFERENCE.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for entry in raw["endpoints"]:
        out[entry["path"]] = {m.upper() for m in entry["methods"]}
    return out


@pytest.fixture(scope="module")
def live_spec() -> dict[str, set[str]]:
    client = TestClient(create_app())
    r = client.get("/openapi.json")
    r.raise_for_status()
    spec = r.json()
    out: dict[str, set[str]] = {}
    for path, ops in spec.get("paths", {}).items():
        methods: set[str] = set()
        for method in ops:
            if method.upper() in {
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
                "HEAD",
                "OPTIONS",
            }:
                methods.add(method.upper())
        if methods:
            out[path] = methods
    return out


def test_no_undocumented_endpoints(
    live_spec: dict[str, set[str]], reference: dict[str, set[str]]
) -> None:
    """Every path+method in the live spec must be in the reference."""
    drift: list[str] = []
    for path, methods in live_spec.items():
        if path not in reference:
            drift.append(f"  + {path} {sorted(methods)} (not in reference)")
            continue
        extra = methods - reference[path]
        if extra:
            drift.append(
                f"  + {path} {sorted(extra)} (methods not in reference)"
            )
    assert not drift, (
        "Live OpenAPI exposes endpoints not declared in "
        "openapi_phase0.yaml. Update docs/API.md and the reference, "
        "or remove the route:\n" + "\n".join(drift)
    )


def test_no_missing_endpoints(
    live_spec: dict[str, set[str]], reference: dict[str, set[str]]
) -> None:
    """Every path+method in the reference must be served by the app."""
    drift: list[str] = []
    for path, methods in reference.items():
        if path not in live_spec:
            drift.append(f"  - {path} {sorted(methods)} (missing from spec)")
            continue
        missing = methods - live_spec[path]
        if missing:
            drift.append(
                f"  - {path} {sorted(missing)} (methods missing from spec)"
            )
    assert not drift, (
        "openapi_phase0.yaml lists endpoints the app doesn't serve. "
        "Either implement them or remove from the reference:\n"
        + "\n".join(drift)
    )


def test_phase0_surface_is_at_least_n_paths(
    reference: dict[str, set[str]],
) -> None:
    """Smoke check: the reference covers the bulk of Phase-0 endpoints."""
    assert len(reference) >= 18  # auth(5) + companies(3) + ledgers(2) + vouchers(3) + audit(1) + connector(4) + 2 placeholders

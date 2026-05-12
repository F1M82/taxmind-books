"""Validation report collector.

Spec: ``docs/VALIDATION_REPORT.md`` (frozen). This implementation matches
the CLI surface, output shape, and "evidence not assertions" philosophy
of that doc. Differences from the inlined spec:

- The doc was frozen before the v1.2 amendment added P0.36–P0.46. The
  task table in Section 8 covers all 46 tasks.
- Subprocess calls auto-route through ``backend/.venv/Scripts/`` so the
  human can ``python tools/validation_report/collect_report.py …`` from
  the repo root with the system Python — without manually activating
  the backend venv first.
- ``httpx`` and ``psycopg`` are lazy-imported; if either is missing the
  affected section reports "skipped" rather than aborting the whole
  collection. The doc's philosophy is to record gaps in the artefact.

Usage::

    python tools/validation_report/collect_report.py --phase 0 \\
        --out validation/ --base-url http://localhost:8000

Outputs ``validation/phase_<N>_<timestamp>.md``. Sections 1–6 are
auto-collected; 7–9 are blank scaffolding for the human reviewer.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------
# venv discovery
# ---------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
BACKEND_VENV_BIN = (
    BACKEND_DIR / ".venv" / ("Scripts" if os.name == "nt" else "bin")
)


def _venv_tool(name: str) -> str:
    """Return the path to a tool in the backend venv, else PATH lookup."""
    exe = name + (".exe" if os.name == "nt" else "")
    candidate = BACKEND_VENV_BIN / exe
    if candidate.exists():
        return str(candidate)
    fallback = shutil.which(name)
    return fallback or name


PYTHON = _venv_tool("python")
PYTEST = _venv_tool("pytest")
RUFF = _venv_tool("ruff")
MYPY = _venv_tool("mypy")
ALEMBIC = _venv_tool("alembic")


# ---------------------------------------------------------------------
# Command runner
# ---------------------------------------------------------------------


def run_command(
    cmd: Sequence[str], cwd: Path | None = None
) -> dict[str, Any]:
    """Run a command, capture stdout/stderr/exit, return structured result.

    Stdout and stderr are trimmed to the last 5000 chars to keep the
    final report manageable. Timeouts (10 min) and FileNotFoundError
    (tool missing) are both surfaced as exit -1 with the reason in
    stderr.
    """
    try:
        result = subprocess.run(  # noqa: S603 — inputs are hardcoded
            list(cmd),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        return {
            "command": " ".join(cmd),
            "exit_code": result.returncode,
            "stdout": result.stdout[-5000:],
            "stderr": result.stderr[-5000:],
        }
    except subprocess.TimeoutExpired:
        return {
            "command": " ".join(cmd),
            "exit_code": -1,
            "stdout": "",
            "stderr": "TIMEOUT (10 minutes)",
        }
    except FileNotFoundError as exc:
        return {
            "command": " ".join(cmd),
            "exit_code": -1,
            "stdout": "",
            "stderr": f"executable not found: {exc}",
        }


# ---------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------


def collect_environment() -> dict[str, Any]:
    return {
        "python_version": platform.python_version(),
        "node_version": run_command(["node", "--version"])["stdout"].strip()
        or "not found",
        "os": platform.platform(),
        "git_sha": run_command(["git", "rev-parse", "HEAD"])["stdout"].strip(),
        "git_branch": run_command(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"]
        )["stdout"].strip(),
        "git_status_clean": run_command(["git", "status", "--porcelain"])[
            "stdout"
        ].strip()
        == "",
        "database_url_set": bool(os.environ.get("DATABASE_URL")),
        "redis_url_set": bool(os.environ.get("REDIS_URL")),
        "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "backend_venv_present": BACKEND_VENV_BIN.exists(),
        "timestamp": datetime.now(UTC).isoformat(),
    }


def collect_static_checks() -> dict[str, Any]:
    return {
        "lint_ruff": run_command([RUFF, "check", "."], cwd=BACKEND_DIR),
        "format_check": run_command(
            [RUFF, "format", "--check", "."], cwd=BACKEND_DIR
        ),
        "type_check": run_command(
            [MYPY, "app/", "--strict"], cwd=BACKEND_DIR
        ),
        "lint_money": run_command(
            [PYTHON, str(REPO_ROOT / "tools" / "lint" / "check_money_types.py"),
             "app"],
            cwd=BACKEND_DIR,
        ),
        "lint_audit": run_command(
            [PYTHON, str(REPO_ROOT / "tools" / "lint" / "check_audit_emit.py"),
             "app/services"],
            cwd=BACKEND_DIR,
        ),
        "lint_imports": run_command(
            [PYTHON, str(REPO_ROOT / "tools" / "lint" / "check_imports.py"),
             "app"],
            cwd=BACKEND_DIR,
        ),
    }


def collect_test_results() -> dict[str, Any]:
    return {
        "unit_tests": run_command(
            [PYTEST, "tests/unit/", "--no-header", "--tb=short"],
            cwd=BACKEND_DIR,
        ),
        "integration_tests": run_command(
            [PYTEST, "tests/integration/", "--no-header", "--tb=short"],
            cwd=BACKEND_DIR,
        ),
        "tenant_isolation_tests": run_command(
            [
                PYTEST,
                "tests/tenant_isolation/",
                "--no-header",
                "--tb=short",
            ],
            cwd=BACKEND_DIR,
        ),
        "coverage": run_command(
            [
                PYTEST,
                "--cov=app",
                "--cov-report=term",
                "--no-header",
                "tests/",
            ],
            cwd=BACKEND_DIR,
        ),
    }


def collect_migration_results() -> dict[str, Any]:
    """Round-trip the migrations against the configured DATABASE_URL."""
    return {
        "alembic_upgrade_head": run_command(
            [ALEMBIC, "upgrade", "head"], cwd=BACKEND_DIR
        ),
        "alembic_downgrade_base": run_command(
            [ALEMBIC, "downgrade", "base"], cwd=BACKEND_DIR
        ),
        "alembic_upgrade_again": run_command(
            [ALEMBIC, "upgrade", "head"], cwd=BACKEND_DIR
        ),
        "alembic_check": run_command(
            [ALEMBIC, "check"], cwd=BACKEND_DIR
        ),
    }


def smoke_test_endpoints(base_url: str) -> dict[str, Any]:
    """Hit a handful of unauthenticated endpoints to confirm the app is up.

    Lazy-imports ``httpx`` so the script still runs (with the section
    marked skipped) when httpx is absent.
    """
    try:
        import httpx
    except ImportError as exc:
        return {"__skipped__": f"httpx not available: {exc}"}

    results: dict[str, Any] = {}
    for path in ["/", "/health", "/api/v1/health", "/docs"]:
        try:
            r = httpx.get(base_url + path, timeout=5.0)
            results[path] = {
                "status": r.status_code,
                "ok": r.status_code < 500,
            }
        except Exception as exc:  # broad — any transport failure is "down"
            results[path] = {
                "status": None,
                "ok": False,
                "error": str(exc),
            }
    return results


def db_introspection() -> dict[str, Any]:
    """Check schema is in expected state.

    Lazy-imports ``psycopg`` and skips cleanly when the DSN or the
    library isn't available.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return {"__skipped__": "DATABASE_URL not set"}

    try:
        import psycopg
    except ImportError as exc:
        return {"__skipped__": f"psycopg not available: {exc}"}

    # SQLAlchemy-style DSNs ("postgresql+psycopg://…") aren't valid for
    # the bare psycopg driver; strip the dialect prefix.
    if dsn.startswith("postgresql+psycopg://"):
        dsn = "postgresql://" + dsn[len("postgresql+psycopg://"):]

    try:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
            tables = [r[0] for r in cur.fetchall()]

            cur.execute(
                "SELECT trigger_name FROM information_schema.triggers "
                "WHERE event_object_table = 'audit_logs'"
            )
            audit_triggers = sorted({r[0] for r in cur.fetchall()})

            cur.execute(
                """
                SELECT table_name, column_name, numeric_precision,
                       numeric_scale
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND data_type = 'numeric'
                  AND (column_name LIKE '%amount%'
                       OR column_name LIKE '%balance%'
                       OR column_name IN ('cgst', 'sgst', 'igst',
                                          'cess', 'tds_amount'))
                ORDER BY table_name, column_name
                """
            )
            money_columns = [
                {
                    "table": t,
                    "column": c,
                    "precision": p,
                    "scale": s,
                }
                for t, c, p, s in cur.fetchall()
            ]

            cur.execute(
                "SELECT version_num FROM alembic_version"
            )
            row = cur.fetchone()
            alembic_version = row[0] if row else None

            return {
                "tables": tables,
                "audit_triggers": audit_triggers,
                "money_columns": money_columns,
                "alembic_version": alembic_version,
            }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------

PHASE_0_TASKS = [
    ("P0.01", "Repo bootstrap"),
    ("P0.02", "Backend skeleton"),
    ("P0.03", "Alembic"),
    ("P0.04", "Money primitives"),
    ("P0.05", "Models User/Company"),
    ("P0.06", "Models Ledger"),
    ("P0.07", "Models Voucher"),
    ("P0.08", "Models Audit/Idem"),
    ("P0.09", "Auth security"),
    ("P0.10", "Tenancy deps"),
    ("P0.11", "Audit emitter"),
    ("P0.12", "Idempotency handler"),
    ("P0.13", "Error handling"),
    ("P0.14", "Auth register"),
    ("P0.15", "Auth login/refresh/me/password"),
    ("P0.16", "Companies CRUD"),
    ("P0.17", "Ledgers CRUD"),
    ("P0.18", "Vouchers create"),
    ("P0.19", "Vouchers read/update/cancel"),
    ("P0.20", "Audit log read API"),
    ("P0.21", "Connector tally_client salvage"),
    ("P0.22", "Connector WS client"),
    ("P0.23", "Connector enrollment"),
    ("P0.24", "Connector WS endpoint"),
    ("P0.25", "Connector status"),
    ("P0.26", "Voucher dispatcher"),
    ("P0.27", "Sync trigger"),
    ("P0.28", "Connector PyInstaller"),
    ("P0.29", "Mobile auth screens"),
    ("P0.30", "Mobile company switcher"),
    ("P0.31", "Mobile voucher entry"),
    ("P0.32", "CI pipeline"),
    ("P0.33", "Lint imports"),
    ("P0.34", "OpenAPI contract test"),
    ("P0.35", "README/setup"),
    ("P0.36", "Manual voucher entry (8 types)"),
    ("P0.37", "Voucher Optional/Regular fields"),
    ("P0.38", "Reports endpoints"),
    ("P0.39", "Mobile reports screens"),
    ("P0.40", "Dashboard endpoint"),
    ("P0.41", "Mobile dashboard screen"),
    ("P0.42", "Onboarding checklist endpoint"),
    ("P0.43", "Mobile onboarding checklist"),
    ("P0.44", "Push notification infra"),
    ("P0.45", "Account deletion (DPDP)"),
    ("P0.46", "Connector Optional voucher flow"),
]


def _verdict(exit_code: int) -> str:
    return "PASS" if exit_code == 0 else "FAIL"


def _block(stdout: str, max_chars: int = 3000) -> str:
    """Format a captured stdout block for the report."""
    body = stdout[-max_chars:].rstrip()
    return f"```\n{body}\n```" if body else "_(no output)_"


def write_report(
    out_path: Path,
    phase: int,
    base_url: str,
    env: dict[str, Any],
    static: dict[str, Any],
    tests: dict[str, Any],
    migrations: dict[str, Any],
    smokes: dict[str, Any],
    db: dict[str, Any],
) -> None:
    parts: list[str] = []
    parts.append(
        f"# Validation Report — Phase {phase}\n\n"
        f"**Generated:** {env['timestamp']}  \n"
        f"**Git SHA:** `{env['git_sha']}`  \n"
        f"**Branch:** `{env['git_branch']}`  \n"
        f"**Working tree clean:** {env['git_status_clean']}\n\n"
        "---\n"
    )

    # ----- Section 1 -----
    parts.append(
        "\n## 1. Environment (auto-collected)\n\n"
        "| Field | Value |\n"
        "|---|---|\n"
        f"| Python version | {env['python_version']} |\n"
        f"| Node version | {env['node_version']} |\n"
        f"| OS | {env['os']} |\n"
        f"| DATABASE_URL set | {env['database_url_set']} |\n"
        f"| REDIS_URL set | {env['redis_url_set']} |\n"
        f"| ANTHROPIC_API_KEY set | {env['anthropic_key_set']} |\n"
        f"| backend/.venv present | {env['backend_venv_present']} |\n"
        f"| Smoke base URL | `{base_url}` |\n\n"
        "---\n"
    )

    # ----- Section 2 -----
    static_rows = [
        ("ruff lint", static["lint_ruff"]),
        ("ruff format", static["format_check"]),
        ("mypy strict", static["type_check"]),
        ("money types lint", static["lint_money"]),
        ("audit emit lint", static["lint_audit"]),
        ("import boundaries", static["lint_imports"]),
    ]
    rows = "\n".join(
        f"| {name} | {r['exit_code']} | {_verdict(r['exit_code'])} |"
        for name, r in static_rows
    )
    parts.append(
        "\n## 2. Static checks (auto-collected)\n\n"
        "| Check | Exit | Result |\n|---|---|---|\n"
        f"{rows}\n\n"
        "### Failure details (if any)\n"
    )
    for name, r in static_rows:
        if r["exit_code"] != 0:
            parts.append(f"\n**{name}**\n{_block(r['stdout'] or r['stderr'])}\n")
    parts.append("\n---\n")

    # ----- Section 3 -----
    test_rows = [
        ("unit", tests["unit_tests"]),
        ("integration", tests["integration_tests"]),
        ("tenant isolation", tests["tenant_isolation_tests"]),
    ]
    rows = "\n".join(
        f"| {name} | {r['exit_code']} | {_verdict(r['exit_code'])} |"
        for name, r in test_rows
    )
    parts.append(
        "\n## 3. Test suite (auto-collected)\n\n"
        "| Suite | Exit | Result |\n|---|---|---|\n"
        f"{rows}\n\n"
        "### Coverage\n"
        f"{_block(tests['coverage']['stdout'], 2000)}\n\n"
        "### Test failure details (if any)\n"
    )
    for name, r in test_rows:
        if r["exit_code"] != 0:
            parts.append(f"\n**{name}**\n{_block(r['stdout'])}\n")
    parts.append("\n---\n")

    # ----- Section 4 -----
    mig_rows = [
        ("upgrade head", migrations["alembic_upgrade_head"]),
        ("downgrade base", migrations["alembic_downgrade_base"]),
        ("upgrade head again", migrations["alembic_upgrade_again"]),
        ("alembic check", migrations["alembic_check"]),
    ]
    rows = "\n".join(
        f"| {name} | {r['exit_code']} | {_verdict(r['exit_code'])} |"
        for name, r in mig_rows
    )
    parts.append(
        "\n## 4. Migration round-trip (auto-collected)\n\n"
        "| Step | Exit | Result |\n|---|---|---|\n"
        f"{rows}\n\n"
        "---\n"
    )

    # ----- Section 5 -----
    parts.append("\n## 5. Smoke tests (auto-collected)\n\n")
    if "__skipped__" in smokes:
        parts.append(f"_Skipped: {smokes['__skipped__']}_\n")
    else:
        for path, r in smokes.items():
            verdict = "ok" if r.get("ok") else "FAIL"
            extra = (
                f" — {r['error']}" if r.get("error") else ""
            )
            parts.append(f"- `{path}` → {r.get('status')} ({verdict}){extra}\n")
    parts.append("\n---\n")

    # ----- Section 6 -----
    parts.append("\n## 6. Database introspection (auto-collected)\n\n")
    if "__skipped__" in db:
        parts.append(f"_Skipped: {db['__skipped__']}_\n")
    elif "error" in db:
        parts.append(f"_Error: {db['error']}_\n")
    else:
        parts.append(
            f"**Alembic head:** `{db.get('alembic_version') or 'unknown'}`\n\n"
            "### Tables present\n"
            f"{', '.join(db.get('tables', [])) or '_(none)_'}\n\n"
            "### Audit-log triggers\n"
            f"{', '.join(db.get('audit_triggers', [])) or '_(none)_'}\n\n"
            "### Money columns\n"
            "| Table | Column | Precision | Scale |\n"
            "|---|---|---|---|\n"
        )
        for c in db.get("money_columns", []):
            parts.append(
                f"| {c['table']} | {c['column']} | "
                f"{c['precision']} | {c['scale']} |\n"
            )
        parts.append(
            "\nExpected: ALL money columns must show "
            "precision=15, scale=2.\n"
        )
    parts.append("\n---\n")

    # ----- Section 7 -----
    parts.append(
        "\n## 7. Manual verification (HUMAN FILLS IN)\n\n"
        "Walk through each scenario; record observations in the Notes "
        "block. The script does not assert; the human decides "
        "pass/fail/partial.\n\n"
        "### 7.1 Money handling (per MONEY.md)\n"
        "- [ ] POST /vouchers/ with `total_amount: 1500.99` (float in "
        "JSON) → expected 422\n"
        "- [ ] POST /vouchers/ with `\"total_amount\": \"1500.99\"` "
        "(string) → expected 201, response has string\n"
        "- [ ] POST /vouchers/ with `\"total_amount\": \"1500.999\"` "
        "(3 dp) → expected 422\n"
        "- [ ] DB inspect: `SELECT total_amount FROM vouchers LIMIT 1` "
        "shows exact value, no float artifact\n\n"
        "Notes:\n\n"
        "### 7.2 Tenant isolation (per TENANCY.md)\n"
        "- [ ] User A in company A only; GET /vouchers/{id} for a "
        "voucher in company B → expected 404\n"
        "- [ ] User A; POST /vouchers/ with X-Company-ID = B → "
        "expected 404\n"
        "- [ ] User A; POST with body `company_id: \"<B>\"` and "
        "X-Company-ID = A → expected 201 with company_id=A OR 422\n"
        "- [ ] User A; GET /vouchers/ without X-Company-ID → expected "
        "422\n"
        "- [ ] User A; GET /vouchers/?company_id=<B> → query param "
        "ignored, only A's data returned\n\n"
        "Notes:\n\n"
        "### 7.3 Audit log (per AUDIT.md)\n"
        "- [ ] Create voucher; query audit_logs → exactly 1 row, "
        "action='voucher.created', new_value contains total_amount "
        "as string\n"
        "- [ ] Update narration; query audit_logs → 2nd row, changes "
        "contains only narration\n"
        "- [ ] Cancel voucher; 3rd row with action='voucher.cancelled'\n"
        "- [ ] As app user, run `UPDATE audit_logs SET action='x' "
        "WHERE id=...` → expected: trigger raises exception\n"
        "- [ ] As viewer-role user, GET /audit-logs/ → expected 403\n"
        "- [ ] No audit log row contains literal password/token/"
        "api_key value\n\n"
        "Notes:\n\n"
        "### 7.4 Idempotency (per IDEMPOTENCY.md)\n"
        "- [ ] POST /vouchers/ with Idempotency-Key K1 → 201\n"
        "- [ ] POST same body, same K1 → 201, same voucher.id, "
        "Idempotent-Replay: true header\n"
        "- [ ] POST different body, same K1 → 409 idempotency_replay\n"
        "- [ ] POST without Idempotency-Key header → 400 "
        "idempotency_key_required\n"
        "- [ ] POST K2 to /vouchers/, then K2 to /ingestions/ → 409 "
        "idempotency_key_misuse\n"
        "- [ ] DB inspect: only 1 voucher created across all retries\n\n"
        "Notes:\n\n"
        "### 7.5 Tally Connector (per CONNECTOR_PROTOCOL.md)\n"
        "- [ ] Install connector on Windows VM with Tally running → "
        "registers within 5 seconds\n"
        "- [ ] GET /connector/status → connected: true, "
        "tally_running: true\n"
        "- [ ] Stop Tally → next heartbeat shows tally_running: false\n"
        "- [ ] POST voucher while Tally stopped → voucher in DB, "
        "audit log shows tally_post_failed, retry queued\n"
        "- [ ] Start Tally → next retry succeeds, tally_posted_at "
        "populated\n"
        "- [ ] Disconnect network on connector PC for 2 min → "
        "backend marks connected: false after 90s, reconnects "
        "automatically\n"
        "- [ ] Same Idempotency-Key replayed → only 1 Tally voucher "
        "created (verify in TallyPrime)\n"
        "- [ ] sync_masters → all Tally ledgers appear in `ledgers` "
        "table\n"
        "- [ ] sync_masters again → no duplicate ledgers (idempotent)\n"
        "- [ ] Backend issues command with wrong company_id → "
        "connector rejects, logs locally\n\n"
        "Notes:\n\n"
        "### 7.6 End-to-end (the Phase 0 deliverable)\n"
        "- [ ] Mobile app: register new user\n"
        "- [ ] Mobile app: log in\n"
        "- [ ] Mobile app: create company \"Acme Test\"\n"
        "- [ ] Install connector on Windows; enroll with code from app\n"
        "- [ ] Mobile app shows connector \"Connected\"\n"
        "- [ ] Trigger sync_masters from mobile app → ledgers populate\n"
        "- [ ] Mobile app: create a manual Receipt voucher (Bank Dr "
        "1000, Some Party Cr 1000)\n"
        "- [ ] Voucher appears in TallyPrime within 5 seconds\n"
        "- [ ] Mobile app shows voucher with `tally_posted_at` "
        "timestamp\n"
        "- [ ] Mobile app: view audit log → all actions present\n\n"
        "Notes:\n\n"
        "---\n"
    )

    # ----- Section 8 -----
    parts.append(
        "\n## 8. Findings (HUMAN FILLS IN)\n\n"
        "### Pass / fail / partial per acceptance criterion\n"
        "Map each criterion in PHASE_0_TASKS.md to one of: pass, "
        "fail, partial, not-tested.\n\n"
        "| Task | Status | Note |\n|---|---|---|\n"
    )
    for task_id, label in PHASE_0_TASKS:
        parts.append(f"| {task_id} {label} | | |\n")
    parts.append(
        "\n### Blockers\n"
        "Items that prevent moving to Phase 1. Include reproduction "
        "steps, expected vs actual, logs.\n\n"
        "1.\n\n"
        "2.\n\n"
        "### Non-blockers\n"
        "Items worth fixing but not blocking. Could be polish, minor "
        "bugs, doc gaps.\n\n"
        "1.\n\n"
        "2.\n\n"
        "### Asks for Architect Claude\n"
        "Architecture-level questions arising from validation. These "
        "trigger the Section 1 stop-and-justify flow if affirmative.\n\n"
        "1.\n\n"
        "2.\n\n"
        "---\n"
    )

    # ----- Section 9 -----
    parts.append(
        "\n## 9. Decision (HUMAN FILLS IN)\n\n"
        "- [ ] PASS — proceed to Phase 1\n"
        "- [ ] CONDITIONAL PASS — fix listed non-blockers in parallel "
        "with Phase 1\n"
        "- [ ] FAIL — Coder Claude returns to fix blockers; "
        "re-validate\n\n"
        "Signed: _________________  Date: _________\n"
    )

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"Report written to {out_path}")


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect a Phase-N validation report."
    )
    parser.add_argument("--phase", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)

    print("Collecting environment...")
    env = collect_environment()
    print(f"Running static checks (backend venv: "
          f"{env['backend_venv_present']})...")
    static = collect_static_checks()
    print("Running test suite...")
    tests = collect_test_results()
    print("Running migration round-trip...")
    migrations = collect_migration_results()
    print(f"Smoke-testing {args.base_url}...")
    smokes = smoke_test_endpoints(args.base_url)
    print("Inspecting database...")
    db = db_introspection()

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report_path = args.out / f"phase_{args.phase}_{timestamp}.md"
    write_report(
        report_path,
        args.phase,
        args.base_url,
        env,
        static,
        tests,
        migrations,
        smokes,
        db,
    )
    print("Done. Open the report and fill in the manual sections (7-9).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

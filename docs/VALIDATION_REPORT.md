# Validation Report Template

**Status:** Frozen.

This document defines how the human validates a phase delivered by Coder Claude, and how findings flow back to Architect Claude. The workflow:

1. Coder Claude executes Phase N tasks per `PHASE_0_TASKS.md` (or later phase tasks).
2. Coder Claude declares the phase done — all task acceptance criteria met, CI green.
3. Human runs `tools/validation_report/collect_report.py` against the running environment.
4. Human fills in the manual sections of the generated report.
5. Human delivers the report (paste into chat with Architect Claude, or commit to a `validation/` directory in the repo).
6. Architect Claude reviews; fixes are scoped as patch tasks or new phase work.

## The reporting agent

`tools/validation_report/collect_report.py` is a single Python script the human runs after pulling the latest code. It:

1. Runs the full test suite and captures output
2. Runs lint and type checks
3. Runs migration round-trip
4. Hits a configurable list of HTTP endpoints (smoke tests)
5. Inspects the database for expected rows and constraints
6. Generates a partially filled markdown report at `validation/phase_<N>_<timestamp>.md`
7. Leaves the manual sections blank for the human to fill in

The script does NOT make assertions — it collects evidence. The human reads and fills in pass/fail.

### Script structure

```python
# tools/validation_report/collect_report.py
"""
Validation report collector.

Usage:
    python tools/validation_report/collect_report.py --phase 0 --out validation/

Requires:
    - The backend running locally (or a configurable URL via --base-url)
    - PostgreSQL access (DATABASE_URL env var)
    - All test dependencies installed
"""

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from textwrap import dedent

import httpx
import psycopg


def run_command(cmd: list[str], cwd: str | None = None) -> dict:
    """Run a command, capture stdout/stderr/exit, return structured result."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=600
        )
        return {
            "command": " ".join(cmd),
            "exit_code": result.returncode,
            "stdout": result.stdout[-5000:],   # tail to keep report manageable
            "stderr": result.stderr[-5000:],
        }
    except subprocess.TimeoutExpired:
        return {"command": " ".join(cmd), "exit_code": -1, "stdout": "", "stderr": "TIMEOUT"}
    except FileNotFoundError as e:
        return {"command": " ".join(cmd), "exit_code": -1, "stdout": "", "stderr": str(e)}


def collect_environment() -> dict:
    return {
        "python_version": platform.python_version(),
        "node_version": run_command(["node", "--version"])["stdout"].strip(),
        "os": platform.platform(),
        "git_sha": run_command(["git", "rev-parse", "HEAD"])["stdout"].strip(),
        "git_branch": run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"])["stdout"].strip(),
        "git_status_clean": run_command(["git", "status", "--porcelain"])["stdout"].strip() == "",
        "database_url_set": bool(os.environ.get("DATABASE_URL")),
        "redis_url_set": bool(os.environ.get("REDIS_URL")),
        "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def collect_test_results(backend_dir: Path) -> dict:
    return {
        "lint_ruff": run_command(["ruff", "check", "."], cwd=backend_dir),
        "format_check": run_command(["ruff", "format", "--check", "."], cwd=backend_dir),
        "type_check": run_command(["mypy", "app/", "--strict"], cwd=backend_dir),
        "lint_money": run_command(["python", "../tools/lint/check_money_types.py"], cwd=backend_dir),
        "lint_audit": run_command(["python", "../tools/lint/check_audit_emit.py"], cwd=backend_dir),
        "lint_imports": run_command(["python", "../tools/lint/check_imports.py"], cwd=backend_dir),
        "unit_tests": run_command(
            ["pytest", "tests/unit/", "-v", "--no-header", "--tb=short"],
            cwd=backend_dir,
        ),
        "integration_tests": run_command(
            ["pytest", "tests/integration/", "-v", "--no-header", "--tb=short"],
            cwd=backend_dir,
        ),
        "tenant_isolation_tests": run_command(
            ["pytest", "tests/tenant_isolation/", "-v", "--no-header", "--tb=short"],
            cwd=backend_dir,
        ),
        "coverage": run_command(
            ["pytest", "--cov=app", "--cov-report=term", "--no-header", "tests/"],
            cwd=backend_dir,
        ),
    }


def collect_migration_results(backend_dir: Path) -> dict:
    return {
        "alembic_upgrade_head": run_command(["alembic", "upgrade", "head"], cwd=backend_dir),
        "alembic_downgrade_base": run_command(["alembic", "downgrade", "base"], cwd=backend_dir),
        "alembic_upgrade_again": run_command(["alembic", "upgrade", "head"], cwd=backend_dir),
        "alembic_check": run_command(["alembic", "check"], cwd=backend_dir),
    }


def smoke_test_endpoints(base_url: str) -> dict:
    """Hit a handful of unauthenticated endpoints to confirm the app is up."""
    results = {}
    for path in ["/", "/api/v1/health", "/api/v1/health/ready", "/docs"]:
        try:
            r = httpx.get(base_url + path, timeout=5.0)
            results[path] = {"status": r.status_code, "ok": r.status_code < 500}
        except Exception as e:
            results[path] = {"status": None, "ok": False, "error": str(e)}
    return results


def db_introspection() -> dict:
    """Check schema is in expected state."""
    if not os.environ.get("DATABASE_URL"):
        return {"skipped": "DATABASE_URL not set"}
    try:
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            with conn.cursor() as cur:
                # Expected tables exist
                cur.execute("""
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = 'public' ORDER BY table_name
                """)
                tables = [r[0] for r in cur.fetchall()]

                # Audit-log triggers exist
                cur.execute("""
                    SELECT trigger_name FROM information_schema.triggers
                    WHERE event_object_table = 'audit_logs'
                """)
                audit_triggers = [r[0] for r in cur.fetchall()]

                # Money columns are NUMERIC(15,2)
                cur.execute("""
                    SELECT table_name, column_name, numeric_precision, numeric_scale
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND data_type = 'numeric'
                      AND (column_name LIKE '%amount%' OR column_name LIKE '%balance%'
                           OR column_name IN ('cgst', 'sgst', 'igst', 'cess', 'tds_amount'))
                    ORDER BY table_name, column_name
                """)
                money_columns = cur.fetchall()

                return {
                    "tables": tables,
                    "audit_triggers": audit_triggers,
                    "money_columns": [
                        {"table": t, "column": c, "precision": p, "scale": s}
                        for t, c, p, s in money_columns
                    ],
                }
    except Exception as e:
        return {"error": str(e)}


def write_report(out_path: Path, phase: int, env: dict, tests: dict, migrations: dict,
                 smokes: dict, db: dict) -> None:
    report = dedent(f"""\
        # Validation Report — Phase {phase}

        **Generated:** {env['timestamp']}
        **Git SHA:** `{env['git_sha']}`
        **Branch:** `{env['git_branch']}`
        **Working tree clean:** {env['git_status_clean']}

        ---

        ## 1. Environment (auto-collected)

        | Field | Value |
        |---|---|
        | Python version | {env['python_version']} |
        | Node version | {env['node_version']} |
        | OS | {env['os']} |
        | DATABASE_URL set | {env['database_url_set']} |
        | REDIS_URL set | {env['redis_url_set']} |
        | ANTHROPIC_API_KEY set | {env['anthropic_key_set']} |

        ---

        ## 2. Static checks (auto-collected)

        | Check | Exit | Result |
        |---|---|---|
        | ruff lint | {tests['lint_ruff']['exit_code']} | {'PASS' if tests['lint_ruff']['exit_code'] == 0 else 'FAIL'} |
        | ruff format | {tests['format_check']['exit_code']} | {'PASS' if tests['format_check']['exit_code'] == 0 else 'FAIL'} |
        | mypy strict | {tests['type_check']['exit_code']} | {'PASS' if tests['type_check']['exit_code'] == 0 else 'FAIL'} |
        | money types lint | {tests['lint_money']['exit_code']} | {'PASS' if tests['lint_money']['exit_code'] == 0 else 'FAIL'} |
        | audit emit lint | {tests['lint_audit']['exit_code']} | {'PASS' if tests['lint_audit']['exit_code'] == 0 else 'FAIL'} |
        | import boundaries | {tests['lint_imports']['exit_code']} | {'PASS' if tests['lint_imports']['exit_code'] == 0 else 'FAIL'} |

        ### Failure details (if any)
        ```
        {tests['lint_ruff']['stdout'] if tests['lint_ruff']['exit_code'] != 0 else ''}
        {tests['type_check']['stdout'] if tests['type_check']['exit_code'] != 0 else ''}
        ```

        ---

        ## 3. Test suite (auto-collected)

        | Suite | Exit | Result |
        |---|---|---|
        | unit | {tests['unit_tests']['exit_code']} | {'PASS' if tests['unit_tests']['exit_code'] == 0 else 'FAIL'} |
        | integration | {tests['integration_tests']['exit_code']} | {'PASS' if tests['integration_tests']['exit_code'] == 0 else 'FAIL'} |
        | tenant isolation | {tests['tenant_isolation_tests']['exit_code']} | {'PASS' if tests['tenant_isolation_tests']['exit_code'] == 0 else 'FAIL'} |

        ### Coverage
        ```
        {tests['coverage']['stdout'][-2000:]}
        ```

        ### Test failure details (if any)
        ```
        {tests['unit_tests']['stdout'][-3000:] if tests['unit_tests']['exit_code'] != 0 else ''}
        {tests['integration_tests']['stdout'][-3000:] if tests['integration_tests']['exit_code'] != 0 else ''}
        {tests['tenant_isolation_tests']['stdout'][-3000:] if tests['tenant_isolation_tests']['exit_code'] != 0 else ''}
        ```

        ---

        ## 4. Migration round-trip (auto-collected)

        | Step | Exit | Result |
        |---|---|---|
        | upgrade head | {migrations['alembic_upgrade_head']['exit_code']} | {'PASS' if migrations['alembic_upgrade_head']['exit_code'] == 0 else 'FAIL'} |
        | downgrade base | {migrations['alembic_downgrade_base']['exit_code']} | {'PASS' if migrations['alembic_downgrade_base']['exit_code'] == 0 else 'FAIL'} |
        | upgrade head again | {migrations['alembic_upgrade_again']['exit_code']} | {'PASS' if migrations['alembic_upgrade_again']['exit_code'] == 0 else 'FAIL'} |
        | alembic check | {migrations['alembic_check']['exit_code']} | {'PASS' if migrations['alembic_check']['exit_code'] == 0 else 'FAIL'} |

        ---

        ## 5. Smoke tests (auto-collected)

        {chr(10).join(f"- `{p}` → {r['status']} ({'ok' if r['ok'] else 'FAIL'})" for p, r in smokes.items())}

        ---

        ## 6. Database introspection (auto-collected)

        ### Tables present
        {', '.join(db.get('tables', []))}

        ### Audit-log triggers
        {', '.join(db.get('audit_triggers', []))}

        ### Money columns
        | Table | Column | Precision | Scale |
        |---|---|---|---|
        {chr(10).join(f"| {c['table']} | {c['column']} | {c['precision']} | {c['scale']} |" for c in db.get('money_columns', []))}

        Expected: ALL money columns must show precision=15, scale=2.

        ---

        ## 7. Manual verification (HUMAN FILLS IN)

        These are the scenarios listed in the various `*.md` files under `docs/`.
        For each, run the steps and write what you observed.

        ### 7.1 Money handling (per MONEY.md)
        - [ ] POST /vouchers/ with `total_amount: 1500.99` (float in JSON) → expected 422
        - [ ] POST /vouchers/ with `"total_amount": "1500.99"` (string) → expected 201, response has string
        - [ ] POST /vouchers/ with `"total_amount": "1500.999"` (3 dp) → expected 422
        - [ ] DB inspect: `SELECT total_amount FROM vouchers LIMIT 1` shows exact value, no float artifact

        Notes:

        ### 7.2 Tenant isolation (per TENANCY.md)
        - [ ] User A in company A only; GET /vouchers/{id} for a voucher in company B → expected 404
        - [ ] User A; POST /vouchers/ with X-Company-ID = B → expected 404
        - [ ] User A; POST with body containing `company_id: "<B>"` and X-Company-ID = A → expected 201 with company_id=A OR 422
        - [ ] User A; GET /vouchers/ without X-Company-ID → expected 422
        - [ ] User A; GET /vouchers/?company_id=<B> → query param ignored, only A's data returned

        Notes:

        ### 7.3 Audit log (per AUDIT.md)
        - [ ] Create voucher; query audit_logs → exactly 1 row, action='voucher.created', new_value contains total_amount as string
        - [ ] Update narration; query audit_logs → 2nd row, changes contains only narration
        - [ ] Cancel voucher; 3rd row with action='voucher.cancelled'
        - [ ] As app user, run `UPDATE audit_logs SET action='x' WHERE id=...` → expected: trigger raises exception
        - [ ] As viewer-role user, GET /audit-logs/ → expected 403
        - [ ] No audit log row contains literal password/token/api_key value

        Notes:

        ### 7.4 Idempotency (per IDEMPOTENCY.md)
        - [ ] POST /vouchers/ with Idempotency-Key K1 → 201
        - [ ] POST same body, same K1 → 201, same voucher.id, Idempotent-Replay: true header
        - [ ] POST different body, same K1 → 409 idempotency_replay
        - [ ] POST without Idempotency-Key header → 400 idempotency_key_required
        - [ ] POST K2 to /vouchers/, then K2 to /ingestions/ → 409 idempotency_key_misuse
        - [ ] DB inspect: only 1 voucher created across all retries

        Notes:

        ### 7.5 Tally Connector (per CONNECTOR_PROTOCOL.md)
        - [ ] Install connector on Windows VM with Tally running → registers within 5 seconds
        - [ ] GET /connector/status → connected: true, tally_running: true
        - [ ] Stop Tally → next heartbeat shows tally_running: false
        - [ ] POST voucher while Tally stopped → voucher in DB, audit log shows tally_post_failed, retry queued
        - [ ] Start Tally → next retry succeeds, tally_posted_at populated
        - [ ] Disconnect network on connector PC for 2 min → backend marks connected: false after 90s, reconnects automatically
        - [ ] Same Idempotency-Key replayed → only 1 Tally voucher created (verify in TallyPrime)
        - [x] sync_masters → all Tally ledgers appear in `ledgers` table
        - [ ] sync_masters again → no duplicate ledgers (idempotent)
        - [x] after sync_masters, verify ledgers exist in DB with correct names, groups, and tenant scoping (P0.46b)
        - [ ] Backend issues command with wrong company_id → connector rejects, logs locally

        Notes:
        - 2026-05-16: Phase 0 live validation. sync_masters end-to-end
          confirmed against live TallyPrime. 5 ledgers persisted with
          correct group_name (ABC LTD → Sundry Creditors, Cash →
          Cash-in-Hand, HDFC BANK → Bank Accounts, Profit & Loss A/c
          → Primary, Xyz Ltd → Sundry Debtors). 5 `ledger.created`
          audit rows emitted (one per ledger). Required P0.46c
          (commit f0c5fc0): the original Tally XML envelope
          (`<TYPE>Data</TYPE><ID>Ledger</ID>`) was being rejected by
          TallyPrime ("Unknown Request, cannot be processed"), and
          two latent parser bugs (NAME read as child element instead
          of XML attribute; GSTIN read from REGISTRATIONTYPE which is
          the enum field, not the GSTIN string) would have masked the
          envelope fix. See P0.46c entry in PHASE_0_CLOSEOUT.md.
        - Idempotency re-run (`sync_masters again`) not yet validated;
          remains open under §7.5.

        ### 7.6 End-to-end (the Phase 0 deliverable)
        - [ ] Mobile app: register new user
        - [ ] Mobile app: log in
        - [ ] Mobile app: create company "Acme Test"
        - [ ] Install connector on Windows; enroll with code from app
        - [ ] Mobile app shows connector "Connected"
        - [ ] Trigger sync_masters from mobile app → ledgers populate
        - [ ] Mobile app: create a manual Receipt voucher (Bank Dr 1000, Some Party Cr 1000)
        - [ ] Voucher appears in TallyPrime within 5 seconds
        - [ ] Mobile app shows voucher with `tally_posted_at` timestamp
        - [ ] Mobile app: view audit log → all actions present

        Notes:

        ---

        ## 8. Findings (HUMAN FILLS IN)

        ### Pass / fail / partial per acceptance criterion
        Map each criterion in PHASE_0_TASKS.md to one of: pass, fail, partial, not-tested.

        | Task | Status | Note |
        |---|---|---|
        | P0.01 Repo bootstrap | | |
        | P0.02 Backend skeleton | | |
        | P0.03 Alembic | | |
        | P0.04 Money primitives | | |
        | P0.05 Models User/Company | | |
        | P0.06 Models Ledger | | |
        | P0.07 Models Voucher | | |
        | P0.08 Models Audit/Idem | | |
        | P0.09 Auth security | | |
        | P0.10 Tenancy deps | | |
        | P0.11 Audit emitter | | |
        | P0.12 Idempotency handler | | |
        | P0.13 Error handling | | |
        | P0.14 Auth register | | |
        | P0.15 Auth login/refresh/me/password | | |
        | P0.16 Companies CRUD | | |
        | P0.17 Ledgers CRUD | | |
        | P0.18 Vouchers create | | |
        | P0.19 Vouchers read/update/cancel | | |
        | P0.20 Audit log read API | | |
        | P0.21 Connector tally_client salvage | | |
        | P0.22 Connector WS client | | |
        | P0.23 Connector enrollment | | |
        | P0.24 Connector WS endpoint | | |
        | P0.25 Connector status | | |
        | P0.26 Voucher dispatcher | | |
        | P0.27 Sync trigger | | |
        | P0.28 Connector PyInstaller | | |
        | P0.29 Mobile auth screens | | |
        | P0.30 Mobile company switcher | | |
        | P0.31 Mobile voucher entry | | |
        | P0.32 CI pipeline | | |
        | P0.33 Lint imports | | |
        | P0.34 OpenAPI contract test | | |
        | P0.35 README/setup | | |

        ### Blockers
        Items that prevent moving to Phase 1. Include reproduction steps, expected vs actual, logs.

        1.

        2.

        ### Non-blockers
        Items worth fixing but not blocking. Could be polish, minor bugs, doc gaps.

        1.

        2.

        ### Asks for Architect Claude
        Architecture-level questions arising from validation. These trigger the Section 1 stop-and-justify flow if affirmative.

        1.

        2.

        ---

        ## 9. Decision (HUMAN FILLS IN)

        - [ ] PASS — proceed to Phase 1
        - [ ] CONDITIONAL PASS — fix listed non-blockers in parallel with Phase 1
        - [ ] FAIL — Coder Claude returns to fix blockers; re-validate

        Signed: _________________  Date: _________
    """)
    out_path.write_text(report)
    print(f"Report written to {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--backend-dir", type=Path, default=Path("backend"))
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    print("Collecting environment...")
    env = collect_environment()
    print("Running static checks and tests...")
    tests = collect_test_results(args.backend_dir)
    print("Running migration round-trip...")
    migrations = collect_migration_results(args.backend_dir)
    print(f"Smoke-testing {args.base_url}...")
    smokes = smoke_test_endpoints(args.base_url)
    print("Inspecting database...")
    db = db_introspection()

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    report_path = args.out / f"phase_{args.phase}_{timestamp}.md"
    write_report(report_path, args.phase, env, tests, migrations, smokes, db)
    print("Done. Open the report and fill in the manual sections.")


if __name__ == "__main__":
    main()
```

## How to use the report

### After Coder Claude declares Phase 0 done

1. Pull the latest code, run migrations, start the stack.
2. From the repo root: `python tools/validation_report/collect_report.py --phase 0 --out validation/`
3. Open `validation/phase_0_<timestamp>.md`.
4. Sections 1–6 are filled by the script.
5. Sections 7, 8, 9 require human work. Section 7.1–7.4 is automated (see below); 7.5 onward stays manual.
6. Commit the filled report to the `validation/` directory in the repo (it is a permanent artifact; later phases benefit from being able to look back).
7. Send the report to Architect Claude — paste in chat, or share the file.

### Filling Sections 7.1–7.4 automatically

Sections 7.1 (Money), 7.2 (Tenancy), 7.3 (Audit), and 7.4 (Idempotency) are API-only checks — they don't need a phone, a connector, or a running Tally. The suite at `tests/validation/test_phase0_section7_*.py` automates them: one pytest test per acceptance criterion in this document, with a session-end checklist that paste-fits the report.

Prerequisites:

- A live backend at `http://localhost:8000` (override with `VALIDATION_BASE_URL`).
- Optional: direct Postgres access for the trigger-bypass check (7.3 #4) and the no-duplicate count (7.4 #6). Override with `VALIDATION_DATABASE_URL`; defaults to `postgresql://taxmind:taxmind@localhost:5432/taxmind_books_test`.

Run:

```
# in one shell:
cd backend && uvicorn app.main:app --port 8000

# in another:
pytest tests/validation/ -v
```

The final block of the pytest output is a markdown checklist with `[x]` / `[ ] FAIL —` / `[ ] SKIP —` per criterion, mirroring Sections 7.1–7.4 of the report. Copy it into the filled report verbatim.

Sections 7.5 (Tally Connector) and 7.6 (End-to-end) stay manual — they require real Tally on a Windows VM, a paired connector PC, and the mobile app on a device or simulator. Automation can't substitute for those without rebuilding the stack in test mode, which defeats the purpose of validation.

### Architect Claude's response

Architect Claude reads the report and produces one of:

**(A) Approve to proceed.** All gates passed. Phase 1 task list is generated. The Phase 0 report is filed.

**(B) Patch tasks.** Specific bugs identified, scoped as small follow-up tasks. Coder Claude executes them. A delta validation report is generated and reviewed.

**(C) Phase 0 incomplete.** Major gaps. Coder Claude returns to specific tasks marked failing and rebuilds. A full re-validation follows.

The decision branches based on the severity and quantity of failures. There is no "fail one criterion, pass anyway" — the constitution Section 8 is strict about acceptance gates.

## What "blocker" vs "non-blocker" means

**Blocker:**
- Any tenant isolation failure
- Any audit log integrity failure
- Any money handling failure
- Test suite cannot run
- Migration cannot apply or reverse
- Core endpoint (auth/voucher/connector) doesn't work
- Coverage threshold not met
- The Phase 0 deliverable end-to-end (Section 7.6) cannot be completed

**Non-blocker:**
- Minor UI polish on mobile
- Performance concerns under load not yet at scale
- Documentation typos
- Suboptimal but working error messages
- Edge cases not in the acceptance criteria

If you are uncertain whether something is a blocker, treat it as one and let Architect Claude decide.

## Trust but verify

The reporting agent is software too. The first time you run it, verify its findings against the truth — don't blindly trust "PASS" output until you've confirmed once that pass means pass. After that, treat its auto-collected sections as authoritative for the boring stuff (test results, migration round-trip) and focus your time on the manual verification (Section 7).

## Validation reports archive

Reports go in `validation/`. Filename format: `phase_<N>_<YYYYMMDD>_<HHMMSS>.md`. Reports are committed to git. Over time this directory becomes the project's quality history and is itself a customer-facing asset (CAs reviewing whether to recommend the product will look at validation discipline).

# Connector .exe versioning + rebuild detection -- design

**Date:** 2026-06-14
**Status:** DESIGN ONLY -- no implementation this session.
**Ticket:** memory/phase_0_5_connector_exe_versioning.md (Phase 0.5 hygiene, DECISION 8).
**Goal:** make a built connector binary self-identify the source it was built
from, so a stale `.exe` (the 2026-05-26 and 2026-06-14 traps) is detected
*before* it silently runs as an older connector.

## Scope + guardrails (locked)

- **In scope:** connector package (build-info module + register payload), the
  build process (`installer/build_exe.py`), the `/status` response plumbing
  (`ConnectorConnection` -> `status_for` -> `ConnectorStatusOut`), and a narrow
  cross-check in `tools/dev_stack.ps1`. Plus the additive register-payload note
  in `docs/CONNECTOR_PROTOCOL.md`.
- **Pure observability / operational signal.** No change to vouchers, audit
  trails, or the idempotency cache. The new fields are nullable and additive.
- **No automated `.exe` rebuild.** Detection WARNs and prints the manual rebuild
  command; it never rebuilds (AV scan time, build-env assumptions, cycle time).
  Auto-rebuild is explicitly deferred (see Decisions).
- **ASCII-only** in `dev_stack.ps1` (PS 5.1 reads UTF-8-no-BOM as ANSI; the em
  dash broke the parser last session).

## Current-state inventory

- `connector/connector/__init__.py`: `__version__ = "0.1.0"` (hardcoded product
  version).
- `connector/connector/ws_client.py:203`: register payload sends
  `"connector_version": CONNECTOR_VERSION` (== `__version__`).
- Backend `app/api/v1/connector_ws.py:178-179` (`_handle_register`): copies
  `tally_version` + `connector_version` from the payload onto the
  `ConnectorConnection`.
- `app/services/tally/connector_registry.py`: `ConnectorConnection` holds
  `connector_version` (line 92); `status_for` returns it (line 248).
- `app/schemas/connector.py:47`: `ConnectorStatusOut.connector_version: str | None`.
- `installer/build_exe.py`: builds via PyInstaller CLI args (`--onefile`,
  `--name`, `--paths`, entrypoint). **No `.spec` file.** Single build entry.
- **No build-info file, no SHA stamping anywhere.** `connector_version` is
  asserted as the product version in `test_connector_ws`, `test_connector_status`,
  `test_connector_registry`, `test_dashboard` -- so it must not be repurposed.
- Precedent: `tools/validation_report/collect_report.py:129` already captures
  `git rev-parse HEAD` for the validation report.

---

## Design

### A. Build-time SHA capture (build_exe.py)

`build()` gains a pre-build step that captures provenance and writes it to a
**generated, gitignored** module the connector imports, and a **sidecar JSON**
next to the `.exe` for file-only inspection.

Captured values (all best-effort; never fail the build if git is unavailable):
- `sha`   = `git rev-parse --short=7 HEAD` (run with `cwd=CONNECTOR_DIR`); on
  failure -> `"unknown"`.
- `dirty` = `True` if `git status --porcelain -- connector/connector` is
  non-empty (build includes uncommitted connector changes).
- `built_at` = `datetime.now(UTC).isoformat()`.

```python
# installer/build_exe.py (sketch, inserted in build() before the PyInstaller run)
def _git(args: list[str]) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=CONNECTOR_DIR,
                             capture_output=True, text=True, check=False)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""

def _write_build_info() -> dict:
    sha = _git(["rev-parse", "--short=7", "HEAD"]) or "unknown"
    dirty = bool(_git(["status", "--porcelain", "--", "connector/connector"]))
    built_at = datetime.now(UTC).isoformat()
    info = {"sha": sha, "dirty": dirty, "built_at": built_at}
    # 1) bundled module (imported by the frozen connector)
    gen = CONNECTOR_DIR / "connector" / "_build_info.py"
    gen.write_text(
        '"""Auto-generated at build time. Do not edit; gitignored."""\n'
        f'BUILD_SHA = {sha!r}\nBUILD_DIRTY = {dirty!r}\nBUILT_AT = {built_at!r}\n',
        encoding="utf-8",
    )
    return info
```

The sidecar is written **after** a successful build, next to the artifact:

```python
    info = _write_build_info()        # before PyInstaller (so it gets bundled)
    ... run PyInstaller ...
    (CONNECTOR_DIR / "dist" / "BUILD_INFO.json").write_text(
        json.dumps(info, indent=2), encoding="utf-8")
```

Why two outputs:
- `_build_info.py` is **bundled into the .exe** -> available at *runtime* so the
  connector can report its build over WS (matters for remote/real-user
  connectors where you cannot read their filesystem).
- `dist/BUILD_INFO.json` is a **sidecar** -> lets `dev_stack.ps1` read the SHA
  *without launching the binary or holding a token* (the pre-launch guard, which
  is the whole point -- catch staleness before it runs).

### B. Tracked accessor with a dev fallback

`_build_info.py` is generated + gitignored, so a clean checkout (pytest, running
from source) won't have it. A small **tracked** accessor provides a fallback so
imports never break:

```python
# connector/connector/build_info.py  (tracked)
try:
    from connector._build_info import BUILD_SHA, BUILD_DIRTY, BUILT_AT
except ImportError:        # source checkout / tests: not built
    BUILD_SHA = "dev"
    BUILD_DIRTY = False
    BUILT_AT = None
```

Running from source reports `sha="dev"`; only a built `.exe` carries a real SHA.

### C. Runtime exposure (additive fields, connector_version unchanged)

`connector_version` STAYS `"0.1.0"` (product version; tests + dashboard depend
on it). Two new nullable fields carry build provenance end-to-end:

| Layer | Change |
|---|---|
| `ws_client._send_register` | add `"connector_build_sha": BUILD_SHA` and `"connector_built_at": BUILT_AT` (from `build_info`) to the register payload |
| `connector_ws._handle_register` | `conn.connector_build_sha = payload.get("connector_build_sha")`; `conn.connector_built_at = payload.get("connector_built_at")` |
| `ConnectorConnection` (registry) | two new fields, default `None` |
| `status_for` | include both in the snapshot dict |
| `ConnectorStatusOut` (schema) | `connector_build_sha: str \| None = None`, `connector_built_at: str \| None = None` |

Additive + nullable: existing tests (which never set these) keep passing; remote
connectors surface their build at `GET /api/v1/connector/status`.

Optional (cheap): a `--version` flag on `connector/main.py` that prints
`TaxMindBooksConnector <__version__> (build <BUILD_SHA>[ dirty], built <BUILT_AT>)`
and exits -- lets a human ask the `.exe` "what build are you?" without a full run.

### D. Bring-up cross-check (dev_stack.ps1, narrow)

A new helper reads `connector/dist/BUILD_INFO.json` and compares to the working
tree. Used in `up` (before the connector-launch step) and `status`. **WARN only
-- never blocks launch** (trust the on-disk binary, but make staleness loud).

```
function Get-ConnectorBuildState:
  - sidecar missing                       -> 'unknown'  (binary unstamped / pre-this-feature)
  - sidecar.sha == 'unknown'/dirty==true  -> 'dirty'    (rebuild recommended)
  - git -C $RepoRoot rev-parse --short=7 HEAD == sidecar.sha   -> 'current'
  - git -C $RepoRoot diff --quiet <sidecar.sha> HEAD -- connector/connector
        exit 0 (no connector changes since)  -> 'current'
        exit !=0 (connector changed since)   -> 'stale'
        (sha not resolvable / git error)     -> 'unknown'
```

Output:
- `current` -> `[OK] connector build matches working tree (sha <x>)`
- `stale`   -> `[WARN] connector .exe built from <x> but connector/ changed since (HEAD <y>) -- STALE. Rebuild: connector\.venv\Scripts\python.exe installer\build_exe.py`
- `dirty`   -> `[WARN] connector .exe built from a dirty tree (sha <x>) -- rebuild for a clean provenance`
- `unknown` -> `[WARN] connector build provenance unknown (no dist\BUILD_INFO.json) -- rebuild to stamp it`

The `git diff <sha> HEAD -- connector/connector` test makes staleness *precise*:
an unrelated HEAD advance (no connector change) is NOT flagged, avoiding
false-positive warnings. Falls back to plain SHA-equality + `unknown` if the
recorded SHA isn't a resolvable commit (e.g., shallow clone).

### E. Manual rebuild instructions (automated rebuild out of scope)

```powershell
# from repo root, with the connector venv:
connector\.venv\Scripts\python.exe connector\installer\build_exe.py
# then re-run bring-up:
tools\dev_stack.ps1 -Action up
```

The script does NOT auto-rebuild: PyInstaller builds are slow, may trip AV, and
assume a build-capable environment (PyInstaller installed in the connector
venv). The bounded-tool philosophy says warn + instruct, let the human rebuild.

### F. Migration / first build after this lands

- **Before the first stamped build:** `dist/BUILD_INFO.json` is absent ->
  `dev_stack` reports `unknown` and recommends a rebuild. This is correct and
  desirable: it immediately flags that the current `dist` binary (the stale
  2026-05-21 build) has no provenance.
- **First build:** `build_exe.py` writes `_build_info.py` (bundled) +
  `dist/BUILD_INFO.json`. The new `.exe` reports its SHA over `/status`;
  `dev_stack` reads the sidecar and compares.
- **Tests / source runs:** `_build_info.py` is gitignored/absent -> the accessor
  fallback yields `sha="dev"`. The register payload then carries
  `connector_build_sha="dev"`; existing tests are unaffected (they assert
  `connector_version`, untouched, and ignore the additive field).
- **CI** (`connector-build.yml` runs `build_exe.py`): git is present in the
  checkout, so the CI artifact is stamped with the CI commit SHA -- no workflow
  change required beyond what already invokes `build_exe.py`.
- **.gitignore:** add `connector/connector/_build_info.py`. `connector/dist/` is
  already gitignored (confirmed last session: `connector/dist/connector.pid` is
  ignored), so `BUILD_INFO.json` is covered -- verify during implementation.

---

## Decision summary

| # | Decision | Rationale |
|---|---|---|
| V1 | **New fields `connector_build_sha` + `connector_built_at`; keep `connector_version="0.1.0"`** | Product version is load-bearing in tests/dashboard; build provenance is a distinct concept. Additive + nullable = no breakage. |
| V2 | **Generated `_build_info.py` (gitignored) + tracked `build_info.py` accessor with `"dev"` fallback** | Bundled into the .exe for runtime reporting; never breaks source/test imports; no tracked-file churn. |
| V3 | **Also emit `dist/BUILD_INFO.json` sidecar** | Pre-launch, no-auth, file-only staleness check for `dev_stack` -- catches staleness before the binary runs. |
| V4 | **Build hook in `build_exe.py` (no `.spec`)** | Single existing build entry; minimal surface. |
| V5 | **WARN only, no auto-rebuild** | AV/cycle-time/build-env concerns; bounded-tool philosophy. |
| V6 | **Staleness = `git diff <sha> HEAD -- connector/connector`, not raw SHA equality** | Precise: unrelated HEAD moves don't false-positive. |
| V7 | **`--dirty` provenance flag captured at build** | A build from uncommitted connector changes is a real provenance caveat worth surfacing. |

## Open questions surfaced (for the implementer / reviewer)

1. **Two fields vs one combined string.** Recommend two
   (`connector_build_sha`, `connector_built_at`) -- the script needs the SHA
   discretely for the diff check, and a timestamp is human-useful. Confirm.
2. **`--version` CLI flag on the connector.** Cheap and useful for "what build
   is this .exe?" without a full launch. Recommend yes; confirm it's in scope
   (touches `connector/main.py` argument handling only).
3. **Dirty-tree strictness.** Recommend WARN on dirty (not block). Confirm a
   dirty build should still be launchable (it should, for dev iteration).
4. **`CONNECTOR_PROTOCOL.md` update.** The register payload gains two fields --
   additive, allowed under the protocol's stated extension rules (no version
   bump). Recommend documenting them in the same change. Confirm.

## Effort

Design (this doc) done. Implementation: build hook + accessor + 5 small
plumbing edits (register payload, `_handle_register`, `ConnectorConnection`,
`status_for`, schema) + the `dev_stack.ps1` cross-check + a couple of tests
(build-info fallback; status surfaces the fields). Within the ticket's 1-2
session estimate. No vouchers/audit/idempotency code touched.

*End of design. No code written, no commits, nothing built.*

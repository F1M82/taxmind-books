# Tally Desktop Connector — Enrollment (Path A: manual admin)

Step-by-step runbook to bring the Windows connector online against a backend for **Phase 0 / Phase 0.5 validation**. This is the manual administrator path — no mobile UI, no installer prompts — purely API calls plus environment variables.

**This is now the manual fallback.** The user-friendly flows scoped in `PHASE_0_TASKS.md` P0.47 (mobile enrollment screen) and P0.48 (interactive first-run prompt) are implemented: an owner generates a code in the mobile app (Dashboard → ADMIN → "Add a device"), and the `.exe` prompts for it on first run and writes `.env` itself. See `CLAUDE.md` §"Connector enrollment for local dev". Use this runbook only when the mobile app is unavailable.

---

## Where to install the connector (v1.3)

The connector must run on the **PC that hosts the Tally data folder**. This applies to both TallyPrime editions:

### Silver edition (single-user)

Tally data lives on the user's daily-driver PC under a path like:
```
C:\Users\Public\TallyPrime\Data
```
(or whatever path the user configured during Tally install). The connector runs on this same PC, foreground, alongside Tally.

### Gold edition (multi-user, shared data)

Tally data lives on a designated PC that other Tally clients connect to over the LAN. Path example:
```
D:\TallyData
```
The connector must run on the **data-host PC**, not on the client PCs. Foreground execution is acceptable as long as the data-host PC is regularly used (someone logs in daily). For headless server deployments (where the data-host has no interactive user), Windows Service install is deferred to a later version and currently out of scope.

### TallyPrime Cloud

Not supported in v1.3 or earlier. Marked for future consideration. The data-folder approach used here only works for on-premises Tally installs.

The `tally_data_folder_path` is configured at enrollment time (Step 4 below) and stored alongside the connector token. The connector reads this path to enumerate available Tally companies for the multi-company mapping flow.

---

## Architecture note (read this first)

The connector `.exe` accepts a **connector token** (long-lived JWT), not the **enrollment code**. The code → token exchange happens *outside* the executable. Don't try to pass the raw enrollment code to the .exe — it will fail to connect.

The flow is:

```
  ┌─────────┐  1. issue   ┌─────────┐  2. enroll    ┌──────────┐
  │  Owner  │ ──────────► │ Backend │ ◄─────────── │ Operator │
  │ (mobile │   /code     │  API    │   /enroll    │ at Tally │
  │  app or │             │         │              │  machine │
  │  curl)  │             │         │              │          │
  └─────────┘             └─────────┘              └──────────┘
       │                       │                        │
       │  3. code (string)     │                        │
       └──────────────────────────────────────────────► │
                               │                        │
                               │  4. connector_token,   │
                               │      company_id        │
                               ▼                        ▼
                                                    .env or env vars
                                                    next to .exe
                                                          │
                                                          ▼
                                                 5. TaxMindBooksConnector.exe
                                                          │
                                                          ▼
                                          WS /api/v1/connector/ws (connected)
                                                          │
                                                          ▼
                                          6. (v1.3) list_tally_companies
                                                          │
                                                          ▼
                                          7. (v1.3) mobile: map backend ↔ Tally
```

Steps 1 and 2 are HTTP calls. Step 3 happens out-of-band. Steps 4–5 happen on the operator's machine. Steps 6–7 are v1.3 additions for multi-Tally-company support.

---

## Prerequisites

| Item | Value used in this runbook | How to set |
|---|---|---|
| Backend reachable on LAN | `http://192.168.1.36:8000` | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| Owner user exists with a company | — | Use `POST /api/v1/auth/register`, then `POST /api/v1/companies` |
| `TaxMindBooksConnector.exe` built | `H:\Accounting Project\connector\dist\TaxMindBooksConnector.exe` | `python installer/build_exe.py` from `connector/` |
| Tally Prime running on the operator machine | — | Default ODBC port 9000 |
| **v1.3:** Tally data folder path known | `C:\Users\Public\TallyPrime\Data` | The folder where TallyPrime stores company data |

Adjust the IP / port / paths to match your environment.

---

## Step 1 — Owner: log in and capture the access token

PowerShell on the owner's workstation:

```powershell
$BASE = "http://192.168.1.36:8000"

# OAuth2 password flow — form-encoded, NOT JSON.
$loginResp = Invoke-RestMethod `
  -Uri "$BASE/api/v1/auth/login" `
  -Method Post `
  -ContentType "application/x-www-form-urlencoded" `
  -Body @{ username = "owner@example.com"; password = "•••" }

$ACCESS  = $loginResp.access_token
$REFRESH = $loginResp.refresh_token
```

If you don't yet know the company UUID:

```powershell
$me = Invoke-RestMethod `
  -Uri "$BASE/api/v1/auth/me" `
  -Headers @{ Authorization = "Bearer $ACCESS" }

$me.companies | Format-Table id, name, role
$COMPANY = ($me.companies | Where-Object role -eq "owner")[0].id
```

---

## Step 2 — Owner: issue an enrollment code

```powershell
$codeResp = Invoke-RestMethod `
  -Uri "$BASE/api/v1/connector/enrollment-codes" `
  -Method Post `
  -Headers @{
    Authorization  = "Bearer $ACCESS"
    "X-Company-ID" = $COMPANY
  }

$CODE = $codeResp.code
Write-Host "Enrollment code (15 min TTL): $CODE"
Write-Host "Expires at:                   $($codeResp.expires_at)"
```

The code is a 43-character URL-safe base64 string. It is **one-time use** and **expires in 15 minutes**.

Transmit `$CODE` to the operator on the Tally machine — chat, phone, QR. The raw code is shown to the owner exactly once; the server stores only its SHA-256 hash.

---

## Step 3 — Operator: exchange the code for a connector token

On the Tally machine (PowerShell):

```powershell
$BASE = "http://192.168.1.36:8000"
$CODE = "<paste-the-43-char-code-here>"

$enrollResp = Invoke-RestMethod `
  -Uri "$BASE/api/v1/connector/enroll" `
  -Method Post `
  -ContentType "application/json" `
  -Body (@{ code = $CODE } | ConvertTo-Json)

$TOKEN        = $enrollResp.connector_token
$COMPANY_ID   = $enrollResp.company_id
$CONNECTOR_ID = $enrollResp.connector_id

Write-Host "Connector token (expires in $($enrollResp.expires_in_days) days):"
Write-Host $TOKEN
Write-Host "Bound to company: $COMPANY_ID"
```

The `/enroll` endpoint is **unauthenticated** — the code itself is the credential.

---

## Step 4 — Operator: configure and run the connector

The `.exe` reads config from environment variables (or a `.env` file in its current working directory).

**v1.3 addition:** `TALLY_DATA_FOLDER_PATH` is now a required env var. The connector uses it to enumerate Tally companies for the multi-company mapping flow.

```powershell
$EXE_DIR = "H:\Accounting Project\connector\dist"

# v1.3: identify the Tally data folder. Adjust for your install.
$TALLY_DATA_FOLDER = "C:\Users\Public\TallyPrime\Data"

@"
CONNECTOR_TOKEN=$TOKEN
CONNECTOR_COMPANY_ID=$COMPANY_ID
BACKEND_WS_URL=ws://192.168.1.36:8000/api/v1/connector/ws
TALLY_HOST=localhost
TALLY_PORT=9000
TALLY_DATA_FOLDER_PATH=$TALLY_DATA_FOLDER
LOG_LEVEL=INFO
"@ | Out-File -FilePath "$EXE_DIR\.env" -Encoding utf8

# Run from that directory so .env is picked up.
Set-Location $EXE_DIR
.\TaxMindBooksConnector.exe
```

If `Out-File` produces a BOM (UTF-8 BOM byte sequence `EF BB BF` at the start of the file) and the connector fails with "X variable missing", rewrite the file BOM-less:

```powershell
$lines = @(
  "CONNECTOR_TOKEN=$TOKEN",
  "CONNECTOR_COMPANY_ID=$COMPANY_ID",
  "BACKEND_WS_URL=ws://192.168.1.36:8000/api/v1/connector/ws",
  "TALLY_HOST=localhost",
  "TALLY_PORT=9000",
  "TALLY_DATA_FOLDER_PATH=$TALLY_DATA_FOLDER",
  "LOG_LEVEL=INFO"
)
[System.IO.File]::WriteAllLines("$EXE_DIR\.env", $lines, [System.Text.UTF8Encoding]::new($false))
```

Alternatively, set env vars in the session directly (most reliable):

```powershell
$env:CONNECTOR_TOKEN = $TOKEN
$env:CONNECTOR_COMPANY_ID = $COMPANY_ID
$env:BACKEND_WS_URL = "ws://192.168.1.36:8000/api/v1/connector/ws"
$env:TALLY_HOST = "localhost"
$env:TALLY_PORT = "9000"
$env:TALLY_DATA_FOLDER_PATH = $TALLY_DATA_FOLDER
$env:LOG_LEVEL = "INFO"

.\TaxMindBooksConnector.exe
```

Expected output (truncated):

```
INFO:connector.ws_client:connecting to ws://192.168.1.36:8000/api/v1/connector/ws
INFO:connector.ws_client:registered: connector_id=…, company_id=…
INFO:connector.ws_client:tally_data_folder_path=C:\Users\Public\TallyPrime\Data
INFO:connector.ws_client:heartbeat every 30s
```

---

## Step 5 — Owner: confirm `connected: true`

Back on the owner's workstation, with `$ACCESS` and `$COMPANY` still set:

```powershell
$status = Invoke-RestMethod `
  -Uri "$BASE/api/v1/connector/status" `
  -Headers @{
    Authorization  = "Bearer $ACCESS"
    "X-Company-ID" = $COMPANY
  }

$status | Format-List
```

A healthy snapshot looks like:

```
company_id             : <uuid>
connected              : True
last_seen_at           : 2026-05-14T12:34:56.789012+00:00
tally_running          : True
tally_version          : 4.2
connector_version      : 0.1.0
queued_outbound_count  : 0
```

---

## Step 6 — (v1.3) List Tally companies discovered by the connector

```powershell
$companies = Invoke-RestMethod `
  -Uri "$BASE/api/v1/connector/$CONNECTOR_ID/tally-companies?refresh=true" `
  -Headers @{ Authorization = "Bearer $ACCESS" }

$companies.companies | Format-Table tally_company_identifier, tally_company_name, gstin, mapped_to_backend_company_id
```

Returns the list of Tally companies found in the connector's `TALLY_DATA_FOLDER_PATH`.

---

## Step 7 — (v1.3) Map a backend company to a Tally company

```powershell
$mapping = Invoke-RestMethod `
  -Uri "$BASE/api/v1/companies/$COMPANY/tally-mapping" `
  -Method Post `
  -Headers @{
    Authorization    = "Bearer $ACCESS"
    "X-Company-ID"   = $COMPANY
    "Idempotency-Key" = [guid]::NewGuid().ToString()
  } `
  -ContentType "application/json" `
  -Body (@{
    connector_id              = $CONNECTOR_ID
    tally_data_folder_path    = $TALLY_DATA_FOLDER
    tally_company_identifier  = "10000"      # the Tally company you want this backend company to bind to
    tally_company_display_name = "Acme Traders"
  } | ConvertTo-Json)

$mapping | Format-List
```

Now this backend company can post vouchers — but only when Tally has "Acme Traders" actually open. If a different Tally company is open, vouchers will land in the queue (status `pending_tally_post`) and post automatically when Tally is switched to the right company.

---

## Step 8 — (v1.3) Verify the active Tally company

```powershell
$active = Invoke-RestMethod `
  -Uri "$BASE/api/v1/connector/$CONNECTOR_ID/active-tally-company" `
  -Headers @{ Authorization = "Bearer $ACCESS" }

$active | Format-List
```

Returns whichever Tally company is currently open on the connector PC. The mobile app uses this for the active-company indicator.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `connected: false` even after .exe started | Backend bound to `127.0.0.1`, not `0.0.0.0` | Restart uvicorn with `--host 0.0.0.0` |
| `enrollment_code_not_found` immediately after issuing | Hitting a different backend instance | Verify both calls use the same `$BASE` |
| Connector logs `connection refused` | Backend down, firewall blocking 8000, or wrong IP | `Test-NetConnection 192.168.1.36 -Port 8000` from the operator machine |
| Connector logs `401`/`invalid token` | Token tied to a deleted company, or you reused a code-derived token after server data was reset | Re-issue a fresh code (Step 2) |
| `tally_running: false` in status | Tally not listening on `localhost:9000` | Enable ODBC in Tally → F1 → Settings → Connectivity |
| `CONNECTOR_TOKEN missing` from .exe | `.env` has BOM, or .exe was launched from wrong directory | Use the WriteAllLines fallback or `$env:` direct-set |
| `data_folder_unreadable` from list_tally_companies | `TALLY_DATA_FOLDER_PATH` is wrong or inaccessible | Verify the path exists in File Explorer; ensure the connector runs as a user with read access |
| Mapping returns `tally_company_not_found_in_discovery` | The Tally company identifier wasn't in the connector's last scan | Call `?refresh=true` on the tally-companies endpoint first |
| Voucher post returns `wrong_company_open` | The active Tally company doesn't match the backend company's mapping | Either switch Tally to the right company OR the voucher is queued and will post when you do |

---

## What's missing for production (P0.47, P0.48)

The path above works mechanically but is not safe for end-user onboarding:

1. **Code transmission is out-of-band.** Owner has to copy/paste the code into a chat. P0.47 puts a copy-to-clipboard button in the mobile app and polls status until the connector comes online.
2. **Operator must run PowerShell.** P0.48 replaces the manual `/enroll` call with a first-run prompt inside the `.exe`, persists the token to `%APPDATA%\TaxMindBooks\config.json`, and re-uses it on subsequent launches. The Tally data folder path is also captured at first-run.
3. **No token rotation UX.** When the JWT expires (default ~365 days), the operator hits silent auth failures with no guidance. P0.48's persistent config gives us a place to track expiry and re-prompt.
4. **No automatic Tally folder discovery.** v1.3 requires the operator to know the path. P0.48 can auto-discover by inspecting Tally registry keys or scanning common paths.

Both tasks are blocked on Phase 1 customer onboarding — see `PHASE_0_TASKS.md` for the specs.

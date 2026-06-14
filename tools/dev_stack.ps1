#Requires -Version 5.1
<#
.SYNOPSIS
    TaxMind Books dev-stack lifecycle: bring up / tear down / report status with
    one command. Idempotent; fails loudly; pure orchestration (touches no
    backend/connector/CI source).

.DESCRIPTION
    Implements docs/dev_stack_script_design.md. Verbs:
      up      Docker (postgres+redis) -> backend uvicorn (detached, WMI) ->
              Tally ODBC probe (best-effort) -> connector .exe (minimized).
      down    Stop connector then backend. Leaves Docker + Tally running.
      status  Read-only report. Exit 0 if all up, 1 otherwise.

    -Verify (up only, opt-in): after launch, log in the dev test user and check
    GET /api/v1/connector/status. Credentials come from env vars
    DEV_TEST_USER / DEV_TEST_PASSWORD (never hardcoded). The minted token is
    one-shot; nothing is written to disk.

.NOTES
    Targets Windows PowerShell 5.1. Backend is detached via WMI
    Win32_Process.Create (survives the launching shell); the connector is
    launched via Start-Process -WindowStyle Minimized per
    memory/connector_launch_pyinstaller_stdio.md. The connector .exe is trusted
    as-is - staleness detection is a separate ticket
    (memory/phase_0_5_connector_exe_versioning.md); this script only prints the
    .exe mtime as a breadcrumb.
#>
[CmdletBinding()]
param(
    [ValidateSet('up', 'down', 'status')]
    [string]$Action = 'up',
    [switch]$Verify
)

$ErrorActionPreference = 'Stop'

# --------------------------------------------------------------------------
# Configuration (single source of truth for paths / ports / names)
# --------------------------------------------------------------------------
$RepoRoot         = Split-Path -Parent $PSScriptRoot
$UvicornCmd       = Join-Path $RepoRoot 'backend\logs\run_uvicorn.cmd'
$UvicornLog       = Join-Path $RepoRoot 'backend\logs\uvicorn.log'
$UvicornPidFile   = Join-Path $RepoRoot 'backend\logs\uvicorn.pid'
$ConnectorExe     = Join-Path $RepoRoot 'connector\dist\TaxMindBooksConnector.exe'
$ConnectorEnv     = Join-Path $RepoRoot 'connector\dist\.env'
$ConnectorPidFile = Join-Path $RepoRoot 'connector\dist\connector.pid'

$BackendPort       = 8000
$HealthUrl         = 'http://127.0.0.1:8000/health'
$StatusUrl         = 'http://127.0.0.1:8000/api/v1/connector/status'
$LoginUrl          = 'http://127.0.0.1:8000/api/v1/auth/login'
$PgContainer       = 'taxmind-postgres'
$RedisContainer    = 'taxmind-redis'
$ConnectorProcName = 'TaxMindBooksConnector'

# --------------------------------------------------------------------------
# Output helpers (ASCII markers - robust in the PS 5.1 console code page)
# --------------------------------------------------------------------------
function StepLine { param([int]$N, [int]$Total, [string]$Msg) Write-Host ("[{0}/{1}] {2}" -f $N, $Total, $Msg) -ForegroundColor Cyan }
function OkLine   { param([string]$Msg) Write-Host ("      [OK]   " + $Msg) -ForegroundColor Green }
function WarnLine { param([string]$Msg) Write-Host ("      [WARN] " + $Msg) -ForegroundColor Yellow }
function InfoLine { param([string]$Msg) Write-Host ("      [..]   " + $Msg) -ForegroundColor DarkGray }
function FailNow  { param([string]$Msg) Write-Host ("      [FAIL] " + $Msg) -ForegroundColor Red; throw $Msg }

# --------------------------------------------------------------------------
# Probes (all non-throwing - return values, never abort)
# --------------------------------------------------------------------------
function Test-Listening {
    param([int]$Port)
    try { $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop; return ($null -ne $c) }
    catch { return $false }
}

function Get-ListenerPid {
    param([int]$Port)
    try {
        $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        return $c.OwningProcess
    } catch { return $null }
}

function Test-Tcp {
    param([string]$TcpHost, [int]$Port, [int]$TimeoutMs = 2000)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($TcpHost, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch { return $false }
    finally { $client.Close() }
}

function Get-Health {
    try { return Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 3 -ErrorAction Stop }
    catch { return $null }
}

function Read-DotEnv {
    param([string]$Path)
    $h = @{}
    if (-not (Test-Path $Path)) { return $h }
    foreach ($line in Get-Content -Path $Path) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $idx = $t.IndexOf('=')
        if ($idx -lt 1) { continue }
        $k = $t.Substring(0, $idx).Trim()
        $v = $t.Substring($idx + 1).Trim()
        if ($v.Length -ge 2 -and (($v[0] -eq '"' -and $v[-1] -eq '"') -or ($v[0] -eq "'" -and $v[-1] -eq "'"))) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        $h[$k] = $v
    }
    return $h
}

function Get-ConnectorProcs { return @(Get-Process -Name $ConnectorProcName -ErrorAction SilentlyContinue) }

function Invoke-Docker {
    # Run `docker` quietly and return @{ Code; Out }. A local
    # ErrorActionPreference override + 2>&1 capture is required because
    # docker (notably `compose up`) writes progress like
    # "Container X Running" to STDERR even on success; under the
    # script-wide 'Stop' preference, a redirected native stderr line
    # becomes a *terminating* error. Args are passed as an explicit array
    # so tokens like '-f' aren't bound as parameters of this function.
    param([string[]]$DockerArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & docker @DockerArgs 2>&1
        return [pscustomobject]@{ Code = $LASTEXITCODE; Out = $out }
    } finally { $ErrorActionPreference = $prev }
}

function Test-Docker { return ((Invoke-Docker @('info')).Code -eq 0) }

function Get-ContainerHealth {
    param([string]$Name)
    $r = Invoke-Docker @('inspect', '-f', '{{.State.Health.Status}}', $Name)
    if ($r.Code -ne 0) { return $null }
    if ($null -eq $r.Out) { return $null }
    return ([string]($r.Out | Select-Object -First 1)).Trim()
}

# --------------------------------------------------------------------------
# Connector build staleness (Phase 0.5: .exe versioning)
# --------------------------------------------------------------------------
function Invoke-Git {
    # Like Invoke-Docker: local 'Continue' + 2>&1 capture so native git
    # stderr never becomes a terminating error under the 'Stop' preference.
    param([string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & git -C $RepoRoot @GitArgs 2>&1
        return [pscustomobject]@{ Code = $LASTEXITCODE; Out = $out }
    } finally { $ErrorActionPreference = $prev }
}

function Get-HeadSha {
    $r = Invoke-Git @('rev-parse', '--short=7', 'HEAD')
    if ($r.Code -ne 0) { return '' }
    return ([string]($r.Out | Select-Object -First 1)).Trim()
}

function Test-CommitExists {
    param([string]$Sha)
    # True only if the recorded build sha is a commit object present in this
    # repo. A rebased-away / other-clone / gc'd sha is NOT reachable.
    return ((Invoke-Git @('rev-parse', '--verify', '--quiet', "$Sha^{commit}")).Code -eq 0)
}

function Get-ConnectorDiffExit {
    param([string]$Sha)
    # 0 = connector/connector identical between the build sha and HEAD;
    # 1 = it changed since (so the binary is stale). Other = git error.
    return (Invoke-Git @('diff', '--quiet', $Sha, 'HEAD', '--', 'connector/connector')).Code
}

function Resolve-BuildState {
    # Pure classifier (no IO) so it is unit-testable. Returns one of
    # 'current' | 'stale' | 'dirty' | 'unknown'.
    #   unknown: no sidecar, unreadable/'unknown' sha, OR sha not reachable
    #            from HEAD (rebased away / built on another clone) -- never
    #            false-positive to 'current' when the diff can't resolve.
    #   dirty:   built from uncommitted connector/connector changes.
    #   stale:   connector/connector changed since the build sha.
    #   current: sha == HEAD, or reachable with no connector/connector diff.
    param(
        [bool]$SidecarPresent,
        [string]$SidecarSha,
        [bool]$SidecarDirty,
        [string]$HeadSha,
        [bool]$ShaReachable,
        $DiffExit = $null
    )
    if (-not $SidecarPresent) { return 'unknown' }
    if (-not $SidecarSha -or $SidecarSha -eq 'unknown') { return 'unknown' }
    if ($SidecarDirty) { return 'dirty' }
    if ($HeadSha -and ($SidecarSha -eq $HeadSha)) { return 'current' }
    if (-not $ShaReachable) { return 'unknown' }
    if ($null -eq $DiffExit) { return 'unknown' }
    if ([int]$DiffExit -eq 0) { return 'current' }
    return 'stale'
}

function Get-ConnectorBuildState {
    # IO wrapper around Resolve-BuildState. Reads the sidecar + queries git.
    $sidecar = Join-Path $RepoRoot 'connector\dist\BUILD_INFO.json'
    $head = Get-HeadSha
    if (-not (Test-Path $sidecar)) {
        return [pscustomobject]@{ State = 'unknown'; Sha = ''; HeadSha = $head; BuiltAt = '' }
    }
    $sha = ''
    $dirty = $false
    $builtAt = ''
    try {
        $j = Get-Content -Path $sidecar -Raw | ConvertFrom-Json
        $sha = [string]$j.sha
        $dirty = [bool]$j.dirty
        $builtAt = [string]$j.built_at
    } catch {
        return [pscustomobject]@{ State = 'unknown'; Sha = ''; HeadSha = $head; BuiltAt = '' }
    }
    $reachable = $false
    $diffExit = $null
    if ($sha -and $sha -ne 'unknown') {
        $reachable = Test-CommitExists $sha
        if ($reachable -and (-not $dirty) -and ($sha -ne $head)) {
            $diffExit = Get-ConnectorDiffExit $sha
        }
    }
    $state = Resolve-BuildState -SidecarPresent $true -SidecarSha $sha -SidecarDirty $dirty -HeadSha $head -ShaReachable $reachable -DiffExit $diffExit
    return [pscustomobject]@{ State = $state; Sha = $sha; HeadSha = $head; BuiltAt = $builtAt }
}

function Report-BuildState {
    param($Bs)
    $rebuild = 'Rebuild: connector\.venv\Scripts\python.exe installer\build_exe.py'
    switch ($Bs.State) {
        'current' { OkLine "connector build matches working tree (sha $($Bs.Sha))" }
        'stale'   { WarnLine "connector .exe built from $($Bs.Sha) but connector/connector changed since (HEAD $($Bs.HeadSha)) -- STALE. $rebuild" }
        'dirty'   { WarnLine "connector .exe built from a DIRTY tree (sha $($Bs.Sha); uncommitted connector/connector changes, non-reproducible). Commit, then $rebuild" }
        default   { WarnLine "connector build provenance UNKNOWN (missing/unreadable dist\BUILD_INFO.json, or build sha not reachable from HEAD). $rebuild" }
    }
}

# --------------------------------------------------------------------------
# UP
# --------------------------------------------------------------------------
function Invoke-Up {
    $total = 9
    $cfg = Read-DotEnv $ConnectorEnv

    # [1/9] Docker engine
    StepLine 1 $total 'Docker engine reachable'
    if (Test-Docker) {
        OkLine 'docker info responds'
    } else {
        InfoLine 'Docker not responding; attempting to start Docker Desktop...'
        $dd = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
        if (-not (Test-Path $dd)) { FailNow "Docker not running and Docker Desktop not found at '$dd'. Start Docker manually, then re-run." }
        Start-Process -FilePath $dd | Out-Null
        $deadline = (Get-Date).AddSeconds(90)
        while (-not (Test-Docker)) {
            if ((Get-Date) -gt $deadline) { FailNow 'Docker engine did not become reachable within 90s.' }
            Start-Sleep -Seconds 3
        }
        OkLine 'Docker engine reachable'
    }

    # [2/9] Postgres + Redis healthy
    StepLine 2 $total 'Postgres + Redis healthy'
    Push-Location $RepoRoot
    try { $rc = (Invoke-Docker @('compose', 'up', '-d')).Code }
    finally { Pop-Location }
    if ($rc -ne 0) { FailNow "'docker compose up -d' failed (exit $rc)." }
    $deadline = (Get-Date).AddSeconds(60)
    while ($true) {
        $pg = Get-ContainerHealth $PgContainer
        $rd = Get-ContainerHealth $RedisContainer
        if ($pg -eq 'healthy' -and $rd -eq 'healthy') { break }
        if ((Get-Date) -gt $deadline) { FailNow "Containers not healthy within 60s (postgres='$pg', redis='$rd')." }
        Start-Sleep -Seconds 3
    }
    OkLine "postgres=$pg, redis=$rd"

    # [3/9] Backend port reconciled
    StepLine 3 $total 'Backend port 8000 reconciled'
    $existingHealthy = $false
    if (Test-Listening $BackendPort) {
        $h = Get-Health
        if ($null -ne $h) {
            OkLine 'backend already healthy on :8000 (idempotent - launch will be skipped)'
            $existingHealthy = $true
        } else {
            $ownerPid = Get-ListenerPid $BackendPort
            $proc = $null
            if ($ownerPid) { $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue }
            $pname = 'unknown'
            if ($proc) { $pname = $proc.ProcessName }
            if ($pname -match '^(python|pythonw|uvicorn)$') {
                WarnLine "stale '$pname' (pid $ownerPid) holds :8000 but /health fails - stopping it"
                Stop-Process -Id $ownerPid -Force
                Start-Sleep -Seconds 2
            } else {
                FailNow "Port 8000 is held by '$pname' (pid $ownerPid), not a python/uvicorn process, and it does not answer /health. Refusing to kill it - investigate."
            }
        }
    } else {
        OkLine 'port 8000 free'
    }

    # [4/9] Launch backend (detached via WMI)
    StepLine 4 $total 'Backend launched (detached)'
    if ($existingHealthy) {
        OkLine 'skipped (already healthy)'
    } else {
        if (-not (Test-Path $UvicornCmd)) { FailNow "Launcher not found: $UvicornCmd" }
        $cmdLine = 'cmd.exe /c "' + $UvicornCmd + '"'
        $res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmdLine }
        if ($res.ReturnValue -ne 0) { FailNow "Win32_Process.Create failed (ReturnValue=$($res.ReturnValue))." }
        Set-Content -Path $UvicornPidFile -Value $res.ProcessId -Encoding ASCII
        OkLine "launched via WMI (cmd wrapper pid $($res.ProcessId)); awaiting /health"
    }

    # [5/9] Backend /health
    StepLine 5 $total 'Backend /health'
    if ($existingHealthy) {
        OkLine 'already verified'
    } else {
        $deadline = (Get-Date).AddSeconds(30)
        $h = $null
        while ($true) {
            $h = Get-Health
            if ($null -ne $h) { break }
            if ((Get-Date) -gt $deadline) {
                Write-Host '      ---- last 25 lines of uvicorn.log ----' -ForegroundColor DarkGray
                if (Test-Path $UvicornLog) { Get-Content -Path $UvicornLog -Tail 25 | ForEach-Object { Write-Host ('      | ' + $_) -ForegroundColor DarkGray } }
                FailNow 'Backend /health did not return 200 within 30s.'
            }
            Start-Sleep -Seconds 2
        }
        $envName = ''
        try { $envName = $h.env } catch { }
        OkLine ("status=" + $h.status + " env=" + $envName)
    }

    # [6/9] Tally ODBC probe (best-effort; human-gated)
    StepLine 6 $total 'Tally ODBC (:9000)'
    $tallyHost = 'localhost'
    if ($cfg['TALLY_HOST']) { $tallyHost = $cfg['TALLY_HOST'] }
    $tallyPort = 9000
    if ($cfg['TALLY_PORT']) { $tallyPort = [int]$cfg['TALLY_PORT'] }
    if (Test-Tcp $tallyHost $tallyPort) {
        OkLine "${tallyHost}:${tallyPort} responds"
    } else {
        WarnLine "Tally ODBC not responding on ${tallyHost}:${tallyPort}."
        Write-Host '      In TallyPrime: F12 -> Settings -> Connectivity -> Enable ODBC Server = Yes, Port = 9000' -ForegroundColor Yellow
        Write-Host '      Then re-run: tools\dev_stack.ps1 -Action up' -ForegroundColor Yellow
    }

    # [7/9] Connector credentials
    StepLine 7 $total 'Connector credentials'
    $token = $cfg['CONNECTOR_TOKEN']
    $companyId = $cfg['CONNECTOR_COMPANY_ID']
    if (-not $token) { FailNow "CONNECTOR_TOKEN missing in $ConnectorEnv - connector not enrolled. Run the enroll ceremony (CLAUDE.md, 'Connector enrollment for local dev')." }
    if (-not $companyId) { FailNow "CONNECTOR_COMPANY_ID missing in $ConnectorEnv." }
    OkLine "token present; company $companyId"

    # [8/9] Launch connector (minimized) - leave-and-report if already running
    StepLine 8 $total 'Connector launched (minimized)'
    Report-BuildState (Get-ConnectorBuildState)
    $existingConn = Get-ConnectorProcs
    if ($existingConn.Count -gt 0) {
        $pids = ($existingConn | ForEach-Object { $_.Id }) -join ', '
        OkLine "connector already running (PIDs: $pids) - leaving in place. Use 'down' then 'up' to restart cleanly."
    } else {
        if (-not (Test-Path $ConnectorExe)) { FailNow "Connector exe not found: $ConnectorExe" }
        $mtime = (Get-Item $ConnectorExe).LastWriteTime
        InfoLine ("exe mtime " + $mtime.ToString('yyyy-MM-dd HH:mm') + " (staleness breadcrumb; rebuild detection is a separate ticket)")
        $env:CONNECTOR_TOKEN = $token
        $env:CONNECTOR_COMPANY_ID = $companyId
        if ($cfg['BACKEND_WS_URL']) { $env:BACKEND_WS_URL = $cfg['BACKEND_WS_URL'] }
        if ($cfg['TALLY_HOST'])     { $env:TALLY_HOST = $cfg['TALLY_HOST'] }
        if ($cfg['TALLY_PORT'])     { $env:TALLY_PORT = $cfg['TALLY_PORT'] }
        $p = Start-Process -FilePath $ConnectorExe -WindowStyle Minimized -PassThru
        Set-Content -Path $ConnectorPidFile -Value $p.Id -Encoding ASCII
        OkLine "launched (pid $($p.Id))"
    }

    # [9/9] Connector alive (+ optional -Verify status check)
    StepLine 9 $total 'Connector alive'
    Start-Sleep -Seconds 10
    $conn = Get-ConnectorProcs
    if ($conn.Count -eq 0) { FailNow 'Connector exited within 10s of launch (expired token, or a launch-method issue - see memory/connector_launch_pyinstaller_stdio.md). Launch it manually to see the error.' }
    OkLine ("alive (PIDs: " + (($conn | ForEach-Object { $_.Id }) -join ', ') + ")")

    if ($Verify) {
        $u = $env:DEV_TEST_USER
        $pw = $env:DEV_TEST_PASSWORD
        if (-not $u -or -not $pw) {
            FailNow "-Verify is set but DEV_TEST_USER / DEV_TEST_PASSWORD are not in the environment. Set them first, e.g.:`n        `$env:DEV_TEST_USER='you@example.com'; `$env:DEV_TEST_PASSWORD='...'"
        }
        $access = $null
        try {
            $tok = Invoke-RestMethod -Uri $LoginUrl -Method Post -Body @{ username = $u; password = $pw } -ContentType 'application/x-www-form-urlencoded' -TimeoutSec 5 -ErrorAction Stop
            $access = $tok.access_token
        } catch { FailNow "Login failed for '$u': $($_.Exception.Message)" }
        if (-not $access) { FailNow 'Login returned no access_token.' }
        $deadline = (Get-Date).AddSeconds(15)
        $connected = $false
        $tallyRunning = $false
        while ($true) {
            try {
                $st = Invoke-RestMethod -Uri $StatusUrl -Headers @{ Authorization = "Bearer $access"; 'X-Company-ID' = $companyId } -TimeoutSec 5 -ErrorAction Stop
                $connected = [bool]$st.connected
                $tallyRunning = [bool]$st.tally_running
            } catch { }
            if ($connected) { break }
            if ((Get-Date) -gt $deadline) { break }
            Start-Sleep -Seconds 2
        }
        if ($connected) { OkLine "connector status: connected=true, tally_running=$tallyRunning" }
        else { WarnLine 'connector status: connected=false after 15s - registration may lag, or the token may be stale (connector log is silent by design).' }
    }

    Write-Host ''
    Write-Host '=== dev stack UP ===' -ForegroundColor Green
}

# --------------------------------------------------------------------------
# DOWN
# --------------------------------------------------------------------------
function Invoke-Down {
    Write-Host '=== dev stack DOWN ===' -ForegroundColor Cyan

    # Connector first (cleaner WS close).
    $conn = Get-ConnectorProcs
    if ($conn.Count -gt 0) {
        $pids = ($conn | ForEach-Object { $_.Id }) -join ', '
        foreach ($p in $conn) { try { $p.CloseMainWindow() | Out-Null } catch { } }
        Start-Sleep -Seconds 2
        $still = Get-ConnectorProcs
        if ($still.Count -gt 0) { $still | Stop-Process -Force }
        OkLine "connector stopped (PIDs: $pids)"
    } else {
        InfoLine 'connector already down'
    }
    if (Test-Path $ConnectorPidFile) { Remove-Item $ConnectorPidFile -Force }

    # Backend by port (robust regardless of how it was launched).
    $ownerPid = Get-ListenerPid $BackendPort
    if ($ownerPid) {
        Stop-Process -Id $ownerPid -Force
        OkLine "backend stopped (pid $ownerPid on :8000)"
    } else {
        InfoLine 'backend already down (nothing listening on :8000)'
    }
    if (Test-Path $UvicornPidFile) { Remove-Item $UvicornPidFile -Force }

    InfoLine 'Docker (postgres/redis) and Tally left running per convention.'
    Write-Host ''
    Write-Host '=== dev stack DOWN complete ===' -ForegroundColor Green
}

# --------------------------------------------------------------------------
# STATUS (read-only; exit 0 if all up, 1 otherwise)
# --------------------------------------------------------------------------
function Invoke-Status {
    Write-Host '=== dev stack STATUS ===' -ForegroundColor Cyan
    $allUp = $true

    if (Test-Docker) {
        OkLine 'docker engine: reachable'
        $pg = Get-ContainerHealth $PgContainer
        $rd = Get-ContainerHealth $RedisContainer
        if ($pg -eq 'healthy') { OkLine "postgres: $pg" } else { WarnLine "postgres: $pg"; $allUp = $false }
        if ($rd -eq 'healthy') { OkLine "redis: $rd" } else { WarnLine "redis: $rd"; $allUp = $false }
    } else {
        WarnLine 'docker engine: unreachable'
        $allUp = $false
    }

    if (Test-Listening $BackendPort) {
        $h = Get-Health
        if ($null -ne $h) { OkLine "backend: listening :8000, /health status=$($h.status)" }
        else { WarnLine 'backend: :8000 listening but /health not 200'; $allUp = $false }
    } else {
        WarnLine 'backend: not listening on :8000'
        $allUp = $false
    }

    $conn = Get-ConnectorProcs
    if ($conn.Count -gt 0) {
        $pids = ($conn | ForEach-Object { $_.Id }) -join ', '
        $mt = 'exe missing'
        if (Test-Path $ConnectorExe) { $mt = (Get-Item $ConnectorExe).LastWriteTime.ToString('yyyy-MM-dd HH:mm') }
        OkLine "connector: running (PIDs: $pids; exe mtime $mt)"
    } else {
        WarnLine 'connector: not running'
        $allUp = $false
    }
    Report-BuildState (Get-ConnectorBuildState)

    $cfg = Read-DotEnv $ConnectorEnv
    $tallyHost = 'localhost'
    if ($cfg['TALLY_HOST']) { $tallyHost = $cfg['TALLY_HOST'] }
    $tallyPort = 9000
    if ($cfg['TALLY_PORT']) { $tallyPort = [int]$cfg['TALLY_PORT'] }
    if (Test-Tcp $tallyHost $tallyPort) { OkLine "tally: ${tallyHost}:${tallyPort} responds" }
    else { WarnLine "tally: ${tallyHost}:${tallyPort} not responding"; $allUp = $false }

    Write-Host ''
    if ($allUp) { Write-Host '=== all components UP ===' -ForegroundColor Green; exit 0 }
    else { Write-Host '=== one or more components DOWN ===' -ForegroundColor Yellow; exit 1 }
}

# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------
# Guard: only dispatch when executed directly, not when dot-sourced (the
# classifier test dot-sources this file to exercise Resolve-BuildState).
if ($MyInvocation.InvocationName -ne '.') {
    try {
        switch ($Action) {
            'up'     { Invoke-Up }
            'down'   { Invoke-Down }
            'status' { Invoke-Status }
        }
    } catch {
        Write-Host ''
        Write-Host ('FAILED: ' + $_.Exception.Message) -ForegroundColor Red
        exit 1
    }
}

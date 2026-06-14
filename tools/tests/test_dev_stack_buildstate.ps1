#Requires -Version 5.1
# Classifier unit test for dev_stack.ps1 Resolve-BuildState.
# Dot-sources dev_stack.ps1 (the dispatch is guarded so nothing runs) and
# asserts all four states, including the two hardened 'unknown' cases.
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File tools\tests\test_dev_stack_buildstate.ps1

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\dev_stack.ps1"

$script:fail = 0
function Check {
    param([string]$Name, [string]$Expected, [string]$Actual)
    if ($Actual -eq $Expected) {
        Write-Host "[PASS] $Name -> $Actual"
    } else {
        Write-Host "[FAIL] $Name -> got '$Actual', expected '$Expected'" -ForegroundColor Red
        $script:fail++
    }
}

# unknown: no sidecar
Check 'no-sidecar' 'unknown' (Resolve-BuildState -SidecarPresent $false -SidecarSha '' -SidecarDirty $false -HeadSha 'abc1234' -ShaReachable $false -DiffExit $null)
# unknown: sidecar present but sha == 'unknown'
Check 'sha-literal-unknown' 'unknown' (Resolve-BuildState -SidecarPresent $true -SidecarSha 'unknown' -SidecarDirty $false -HeadSha 'abc1234' -ShaReachable $false -DiffExit $null)
# unknown (HARDENING): sha present + differs from HEAD but NOT reachable
#   (rebased away / built on another clone) -> must not false-positive
Check 'sha-unreachable' 'unknown' (Resolve-BuildState -SidecarPresent $true -SidecarSha 'gone123' -SidecarDirty $false -HeadSha 'new5678' -ShaReachable $false -DiffExit $null)
# unknown: reachable but diff couldn't run (defensive null)
Check 'diff-null' 'unknown' (Resolve-BuildState -SidecarPresent $true -SidecarSha 'old1234' -SidecarDirty $false -HeadSha 'new5678' -ShaReachable $true -DiffExit $null)
# dirty: built from uncommitted connector changes (distinct from stale)
Check 'dirty' 'dirty' (Resolve-BuildState -SidecarPresent $true -SidecarSha 'abc1234' -SidecarDirty $true -HeadSha 'abc1234' -ShaReachable $true -DiffExit 0)
# dirty takes precedence even when sha differs from HEAD
Check 'dirty-precedence' 'dirty' (Resolve-BuildState -SidecarPresent $true -SidecarSha 'old1234' -SidecarDirty $true -HeadSha 'new5678' -ShaReachable $true -DiffExit 1)
# current: sha == HEAD
Check 'sha-eq-head' 'current' (Resolve-BuildState -SidecarPresent $true -SidecarSha 'abc1234' -SidecarDirty $false -HeadSha 'abc1234' -ShaReachable $true -DiffExit $null)
# current: sha != HEAD, reachable, no connector/connector diff
Check 'reachable-no-diff' 'current' (Resolve-BuildState -SidecarPresent $true -SidecarSha 'old1234' -SidecarDirty $false -HeadSha 'new5678' -ShaReachable $true -DiffExit 0)
# stale: sha != HEAD, reachable, connector/connector changed since
Check 'reachable-diff' 'stale' (Resolve-BuildState -SidecarPresent $true -SidecarSha 'old1234' -SidecarDirty $false -HeadSha 'new5678' -ShaReachable $true -DiffExit 1)

if ($script:fail -gt 0) {
    Write-Host "FAILED: $($script:fail) case(s)" -ForegroundColor Red
    exit 1
}
Write-Host "ALL PASS (9 cases)" -ForegroundColor Green
exit 0

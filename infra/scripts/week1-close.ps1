param(
    [switch]$SkipExport,
    [switch]$SkipDiscoverySnapshot,
    [switch]$AllowStoppedServices,
    [switch]$InstallScheduledTasks
)

<#
.SYNOPSIS
    Week 1 definition-of-done closure ritual (see docs/WEEK1_DATA_PLATFORM.md).

.DESCRIPTION
    1. Apply migrations (including 0015 gap detector views)
    2. Verify live services (ingest + trader-engine) unless -AllowStoppedServices
    3. Scanner coverage snapshot
    4. Discovery universe snapshot (UTC today)
    5. Gap report (fail if any channel critical)
    6. Optional nightly pg_dump export
#>

$ErrorActionPreference = "Stop"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repo

Write-Host "=== Week 1 close ===" -ForegroundColor Cyan

Write-Host "`n[1/6] Migrations"
powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "migrate.ps1")

Write-Host "`n[2/6] Live services"
$ingest = Get-Process -Name "hyperion-ingest" -ErrorAction SilentlyContinue
$trader = Get-Process -Name "hyperion-trader-engine" -ErrorAction SilentlyContinue
if ($ingest) { Write-Host "  hyperion-ingest: running (PID $($ingest.Id))" -ForegroundColor Green }
else { Write-Host "  hyperion-ingest: not running" -ForegroundColor Yellow }
if ($trader) { Write-Host "  hyperion-trader-engine: running (PID $($trader.Id))" -ForegroundColor Green }
else { Write-Host "  hyperion-trader-engine: not running" -ForegroundColor Yellow }
if (-not $AllowStoppedServices -and (-not $ingest -or -not $trader)) {
    throw "Start live scoop (restart-live-scoop.ps1) or pass -AllowStoppedServices for offline checks only."
}

Write-Host "`n[3/6] Scanner coverage snapshot"
powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "scanner-coverage-snapshot.ps1")

if (-not $SkipDiscoverySnapshot) {
    Write-Host "`n[4/6] Discovery universe snapshot (UTC today)"
    powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "discovery-universe-daily.ps1")
}
else {
    Write-Host "`n[4/6] Discovery snapshot skipped"
}

Write-Host "`n[5/6] Gap report"
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "week1-gap-report.ps1") -FailOnCritical -SkipIngestGaps
if ($LASTEXITCODE -ne 0) { throw "week1-gap-report failed with exit code $LASTEXITCODE" }

if (-not $SkipExport) {
    Write-Host "`n[6/6] Nightly core export"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "week1-nightly-export.ps1")
    if ($LASTEXITCODE -ne 0) { throw "week1-nightly-export failed with exit code $LASTEXITCODE" }
}
else {
    Write-Host "`n[6/6] Export skipped"
}

if ($InstallScheduledTasks) {
    Write-Host "`n[+] Installing Windows scheduled tasks"
    powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "week1-install-scheduled-tasks.ps1")
}

Write-Host "`n=== Week 1 close: PASS ===" -ForegroundColor Green
Write-Host "Reports: exports/week1/reports/"
Write-Host "Dumps:   exports/week1/"
Write-Host "Next:    Week 2 - wallet lifecycle segments (docs/WEEK2_FEATURE_LAYER.md)"

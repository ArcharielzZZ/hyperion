param(
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ps = (Get-Command powershell.exe).Source

function Register-HyperionTask {
    param(
        [string]$Name,
        [string]$ScriptRelative,
        [object]$Trigger
    )
    $scriptPath = Join-Path $repo $ScriptRelative
    $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
    $action = New-ScheduledTaskAction -Execute $ps -Argument $arg -WorkingDirectory $repo
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Settings $settings -Force | Out-Null
    Write-Host "Registered: $Name"
}

function Unregister-HyperionTask {
    param([string]$Name)
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed: $Name (if existed)"
}

$tasks = @(
    "Hyperion-Week1-ScannerSnapshot",
    "Hyperion-Week1-DiscoverySnapshot",
    "Hyperion-Week1-NightlyExport",
    "Hyperion-Week1-GapReport"
)

if ($Unregister) {
    foreach ($t in $tasks) { Unregister-HyperionTask -Name $t }
    exit 0
}

# Every 15 minutes
$scannerTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours(8) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
Register-HyperionTask -Name "Hyperion-Week1-ScannerSnapshot" `
    -ScriptRelative "infra\scripts\scanner-coverage-snapshot.ps1" `
    -Trigger $scannerTrigger

# Daily 00:15 UTC ≈ local depends on machine; document in WEEK1 doc
$discoveryAt = [DateTime]::UtcNow.Date.AddMinutes(15)
if ($discoveryAt -lt [DateTime]::UtcNow) { $discoveryAt = $discoveryAt.AddDays(1) }
$discoveryTrigger = New-ScheduledTaskTrigger -Daily -At $discoveryAt
Register-HyperionTask -Name "Hyperion-Week1-DiscoverySnapshot" `
    -ScriptRelative "infra\scripts\discovery-universe-daily.ps1" `
    -Trigger $discoveryTrigger

# Daily 02:00 local export
$exportTrigger = New-ScheduledTaskTrigger -Daily -At "02:00"
Register-HyperionTask -Name "Hyperion-Week1-NightlyExport" `
    -ScriptRelative "infra\scripts\week1-nightly-export.ps1" `
    -Trigger $exportTrigger

# Hourly gap report (no -FailOnCritical so ops get artifacts even when red)
$gapTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours(8) `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
Register-HyperionTask -Name "Hyperion-Week1-GapReport" `
    -ScriptRelative "infra\scripts\week1-gap-report.ps1" `
    -Trigger $gapTrigger

Write-Host "`nWeek 1 scheduled tasks installed. Open Task Scheduler to confirm."
Write-Host "Unregister: week1-install-scheduled-tasks.ps1 -Unregister"

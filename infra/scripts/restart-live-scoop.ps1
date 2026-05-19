# Restart hyperion-ingest + hyperion-trader-engine with .env loaded (live scoop).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "../..")

$envFile = if (Test-Path ".env") { ".env" } else { ".env.example" }
Get-Content $envFile | Where-Object { $_ -and -not $_.StartsWith("#") } | ForEach-Object {
  $parts = $_ -split "=", 2
  if ($parts.Length -eq 2) {
    Set-Item -Path "Env:$($parts[0].Trim())" -Value $parts[1].Trim()
  }
}

Remove-Item Env:CARGO_TARGET_DIR -ErrorAction SilentlyContinue
Remove-Item Env:HYPERION_TRADER_ENGINE_ONCE -ErrorAction SilentlyContinue

Write-Host "Stopping old hyperion processes..."
Get-Process -Name "hyperion-ingest","hyperion-trader-engine" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

Write-Host "Building ingest + trader-engine..."
cargo build -p hyperion-ingest -p hyperion-trader-engine
if ($LASTEXITCODE -ne 0) { throw "cargo build failed" }

$logDir = Join-Path (Get-Location) "analytics/research/live_scoop_logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$ts = Get-Date -Format "yyyyMMdd_HHmmss"

function Start-HyperionService {
  param([string]$Package, [string]$LogBase)
  $outLog = "$LogBase.out.log"
  $errLog = "$LogBase.err.log"
  Start-Process -FilePath "cargo" -ArgumentList "run","-p",$Package `
    -WorkingDirectory (Get-Location) `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
    -WindowStyle Hidden
}

Write-Host "Starting hyperion-ingest (background)..."
Start-HyperionService -Package "hyperion-ingest" -LogBase (Join-Path $logDir "ingest_$ts")
Start-Sleep -Seconds 3

Write-Host "Starting hyperion-trader-engine (background)..."
Start-HyperionService -Package "hyperion-trader-engine" -LogBase (Join-Path $logDir "trader_engine_$ts")

Start-Sleep -Seconds 8
Write-Host "Logs: $logDir"
Get-Process -Name "hyperion-ingest","hyperion-trader-engine" -ErrorAction SilentlyContinue |
  Format-Table Name, Id, StartTime -AutoSize

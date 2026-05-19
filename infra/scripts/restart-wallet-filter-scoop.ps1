# Restart discovery/scooping with wallet_filter_system gates (migration 0014 + batch filter + trader-engine).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "../..")

$envFile = if (Test-Path ".env") { ".env" } else { ".env.example" }
Get-Content $envFile | Where-Object { $_ -and -not $_.StartsWith("#") } | ForEach-Object {
  $parts = $_ -split "=", 2
  if ($parts.Length -eq 2) {
    Set-Item -Path "Env:$($parts[0].Trim())" -Value $parts[1].Trim()
  }
}
if (-not $env:DATABASE_URL) { throw "DATABASE_URL required" }

Write-Host "=== 1/5 Apply migrations (includes 0014 wallet filter) ==="
powershell -ExecutionPolicy Bypass -File infra/scripts/migrate.ps1

Write-Host "=== 2/5 Reset filter state + demote old discovery promotions ==="
Push-Location python
python main.py wallet-filter-reset
Pop-Location

Write-Host "=== 3/5 Batch wallet-filter evaluate (new gates; sync discovery) ==="
Push-Location python
python main.py wallet-filter-evaluate-all --sync-discovery
Pop-Location

Write-Host "=== 4/5 Trader-engine single recalculation (scores + discovery refresh) ==="
$env:HYPERION_TRADER_ENGINE_ONCE = "1"
$env:CARGO_TARGET_DIR = "target-scoop-once"
cargo run -p hyperion-trader-engine
Remove-Item Env:HYPERION_TRADER_ENGINE_ONCE -ErrorAction SilentlyContinue
Remove-Item Env:CARGO_TARGET_DIR -ErrorAction SilentlyContinue

Write-Host "=== 5/5 Scoring audit report ==="
powershell -ExecutionPolicy Bypass -File infra/scripts/scoring-audit.ps1

Write-Host "Restart scoop complete."

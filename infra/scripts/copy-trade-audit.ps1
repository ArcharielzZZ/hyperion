$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "../..")

$envFile = if (Test-Path ".env") { ".env" } else { ".env.example" }
Get-Content $envFile | Where-Object { $_ -and -not $_.StartsWith("#") } | ForEach-Object {
  $parts = $_ -split "=", 2
  if ($parts.Length -eq 2) {
    Set-Item -Path "Env:$($parts[0].Trim())" -Value $parts[1].Trim()
  }
}

if (-not $env:DATABASE_URL) {
  throw "DATABASE_URL is required in .env"
}

if (-not $env:PYTHONPATH) {
  $pyRoot = Join-Path (Get-Location) "python/src"
  $env:PYTHONPATH = $pyRoot
}

$cmd = Get-Command psql -ErrorAction SilentlyContinue
$psql = if ($cmd) { $cmd.Source } else { $null }
if (-not $psql) {
  $candidate = Join-Path $env:USERPROFILE "scoop\apps\postgresql\current\bin\psql.exe"
  if (Test-Path $candidate) { $psql = $candidate }
}
if (-not $psql) {
  throw "psql not found in PATH (install PostgreSQL client tools)."
}

function Invoke-CopySqlFile {
  param([string]$SqlPath)
  if (-not (Test-Path $SqlPath)) {
    throw "Missing SQL file at $SqlPath"
  }
  $body = Get-Content -Raw $SqlPath
  $out = & $psql $env:DATABASE_URL -v ON_ERROR_STOP=1 -X -q -c $body 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "psql failed: $out"
  }
  return $out
}

$generated = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss' UTC'")
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss")
$outPath = Join-Path "analytics/research" "copy_trade_audit_$stamp.md"
New-Item -ItemType Directory -Path (Split-Path $outPath) -Force | Out-Null

$lines = [System.Collections.Generic.List[string]]::new()
$null = $lines.Add("# Copy trade diligence audit")
$null = $lines.Add("")
$null = $lines.Add("Generated: **$generated**")
$null = $lines.Add("")
$null = $lines.Add("*Step 1 runs `infra/sql/copy_trade_roundtrip_audit.sql` (Postgres 14+).*")
$null = $lines.Add("")
$null = $lines.Add("## Step 1 — Round-trip completeness (SQL stdout)")
$null = $lines.Add("```")

$sqlPath = Join-Path "infra/sql/copy_trade_roundtrip_audit.sql"
$sqlOut = Invoke-CopySqlFile -SqlPath $sqlPath
foreach ($row in @($sqlOut)) {
  $null = $lines.Add($row)
}
$null = $lines.Add("```")
$null = $lines.Add("")

$tempMd = [System.IO.Path]::GetTempFileName()
try {
  Push-Location python
  try {
    python -m hyperion_pipeline.cli.main paper-replay-promoted --realized-only --slippage-bps 7 --markdown-out $tempMd
    if ($LASTEXITCODE -ne 0) {
      throw "paper-replay-promoted failed ($LASTEXITCODE)"
    }
  }
  finally {
    Pop-Location
  }

  $null = $lines.Add("## Step 2 — Slippage-adjusted FIFO replay (7 bps / leg, realized-only)")
  Get-Content $tempMd | ForEach-Object { $null = $lines.Add($_) }
} finally {
  if (Test-Path $tempMd) { Remove-Item $tempMd -Force }
}

$null = $lines.Add("")
$null = $lines.Add("---")
$null = $lines.Add('*Regenerate via `powershell -ExecutionPolicy Bypass -File infra/scripts/copy-trade-audit.ps1` from `hyperion/`.*')

$content = $lines -join "`r`n"
Set-Content -Path $outPath -Value $content -Encoding UTF8
Write-Host "Wrote $outPath"

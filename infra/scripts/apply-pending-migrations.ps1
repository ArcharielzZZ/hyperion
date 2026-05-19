$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..\.."))

$envFile = if (Test-Path ".env") { ".env" } else { ".env.example" }
Get-Content $envFile | Where-Object { $_ -and -not $_.StartsWith("#") } | ForEach-Object {
    $p = $_ -split "=", 2
    if ($p.Length -eq 2) { Set-Item -Path "Env:$($p[0].Trim())" -Value $p[1].Trim() }
}
if (-not $env:DATABASE_URL) { throw "DATABASE_URL is required" }

function Get-SqlxChecksum([string]$path) {
    $h = [System.Security.Cryptography.SHA384]::Create()
    $b = [System.IO.File]::ReadAllBytes($path)
    return ([BitConverter]::ToString($h.ComputeHash($b)).Replace("-", "").ToLower())
}

$mig = "infra/migrations"
$files = @(
    "$mig/0007_trader_discovery_rankings.sql",
    "$mig/0008_storage_efficiency.sql",
    "$mig/0009_python_pipeline_tables.sql",
    "$mig/0010_week1_data_platform.sql"
)
foreach ($f in $files) {
    Write-Host "Applying $f ..."
    psql $env:DATABASE_URL -v ON_ERROR_STOP=1 -f $f
    if ($LASTEXITCODE -ne 0) { throw "psql failed on $f" }
}

foreach ($pair in @(
        @(1, "$mig/0001_init.sql"),
        @(3, "$mig/0003_ingestion_hardening.sql"),
        @(4, "$mig/0004_behavioral_profiles.sql")
    )) {
    $ver = $pair[0]
    $fp = $pair[1]
    $hex = Get-SqlxChecksum $fp
    $sql = "UPDATE _sqlx_migrations SET checksum = decode('$hex','hex') WHERE version = $ver;"
    psql $env:DATABASE_URL -v ON_ERROR_STOP=1 -c $sql
    if ($LASTEXITCODE -ne 0) { throw "checksum update failed v$ver" }
}

$rows = @(
    @(7, "trader discovery rankings", "$mig/0007_trader_discovery_rankings.sql"),
    @(8, "storage efficiency", "$mig/0008_storage_efficiency.sql"),
    @(9, "python pipeline tables", "$mig/0009_python_pipeline_tables.sql"),
    @(10, "week1 data platform", "$mig/0010_week1_data_platform.sql")
)
foreach ($r in $rows) {
    $ver = $r[0]
    $desc = $r[1]
    $fp = $r[2]
    $hex = Get-SqlxChecksum $fp
    $ins = @"
INSERT INTO _sqlx_migrations (version, description, installed_on, success, checksum, execution_time)
SELECT $ver, '$desc', NOW(), true, decode('$hex','hex'), 0
WHERE NOT EXISTS (SELECT 1 FROM _sqlx_migrations WHERE version = $ver);
"@
    psql $env:DATABASE_URL -v ON_ERROR_STOP=1 -c $ins
    if ($LASTEXITCODE -ne 0) { throw "insert migration record failed v$ver" }
}

sqlx migrate run --source infra/migrations
if ($LASTEXITCODE -ne 0) { throw "sqlx migrate run failed" }
Write-Host "Migrations reconciled; sqlx migrate run completed."

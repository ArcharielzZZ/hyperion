param(
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "week1-common.ps1")
Import-HyperionDatabaseEnv

$repo = Get-HyperionRepoRoot
$outDir = if ($OutputDirectory) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
}
else {
    Join-Path $repo "exports\week1"
}
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outFile = Join-Path $outDir "week1_core_$stamp.dump"

$tables = @(
    "public.traders",
    "public.fills",
    "public.positions",
    "public.pnl_snapshots",
    "public.market_snapshots",
    "public.trader_scores",
    "public.trader_discovery_rankings",
    "public.ingest_connection_events",
    "public.discovery_universe_daily",
    "public.scanner_coverage_snapshots"
)

$tableArgs = @()
foreach ($t in $tables) {
    $tableArgs += "-t"
    $tableArgs += $t
}

if (Get-Command pg_dump -ErrorAction SilentlyContinue) {
    $args = @("-Fc", "-f", $outFile) + $tableArgs + $env:DATABASE_URL
    & pg_dump @args
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump exited with code $LASTEXITCODE"
    }
}
else {
    $db = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "hyperion" }
    $user = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "postgres" }
    $names = docker ps --filter "name=hyperion-postgres" --format "{{.Names}}" 2>$null
    if (-not ($names -match "hyperion-postgres")) {
        throw "pg_dump not found on PATH and container hyperion-postgres is not running. Install PostgreSQL client tools or start Docker Compose."
    }
    $tmp = "/tmp/week1_core_$stamp.dump"
    $inner = @("pg_dump", "-U", $user, "-d", $db, "-Fc", "-f", $tmp) + $tableArgs
    & docker exec hyperion-postgres @inner
    if ($LASTEXITCODE -ne 0) {
        throw "docker pg_dump exited with code $LASTEXITCODE"
    }
    docker cp "hyperion-postgres:$tmp" $outFile
    if ($LASTEXITCODE -ne 0) {
        throw "docker cp failed with code $LASTEXITCODE"
    }
    docker exec hyperion-postgres rm -f $tmp | Out-Null
}

Write-Host "Week 1 core tables export written to $outFile"

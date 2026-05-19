# Shared helpers for Week 1 data-platform scripts (see docs/WEEK1_DATA_PLATFORM.md).
# Dot-source from sibling scripts: . (Join-Path $PSScriptRoot "week1-common.ps1")

$script:HyperionRepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

function Get-HyperionRepoRoot {
    return $script:HyperionRepoRoot
}

function Import-HyperionDatabaseEnv {
    Set-Location (Get-HyperionRepoRoot)
    $envFile = if (Test-Path ".env") { ".env" } else { ".env.example" }
    Get-Content $envFile | Where-Object { $_ -and -not $_.StartsWith("#") } | ForEach-Object {
        $parts = $_ -split "=", 2
        if ($parts.Length -eq 2) {
            Set-Item -Path "Env:$($parts[0].Trim())" -Value $parts[1].Trim()
        }
    }
    if (-not $env:DATABASE_URL) {
        throw "DATABASE_URL is required in .env or .env.example"
    }
}

function Invoke-HyperionSql {
    param(
        [Parameter(Mandatory)]
        [string]$Sql
    )
    if (-not $env:DATABASE_URL) {
        throw "DATABASE_URL is not set"
    }
    if (Get-Command psql -ErrorAction SilentlyContinue) {
        $Sql | & psql $env:DATABASE_URL -v ON_ERROR_STOP=1
        if ($LASTEXITCODE -ne 0) {
            throw "psql exited with code $LASTEXITCODE"
        }
        return
    }
    $db = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "hyperion" }
    $user = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "postgres" }
    $names = docker ps --filter "name=hyperion-postgres" --format "{{.Names}}" 2>$null
    if ($names -match "hyperion-postgres") {
        $Sql | docker exec -i hyperion-postgres psql -U $user -d $db -v ON_ERROR_STOP=1
        if ($LASTEXITCODE -ne 0) {
            throw "docker psql exited with code $LASTEXITCODE"
        }
        return
    }
    throw "Install PostgreSQL client tools (psql), or start the stack so container hyperion-postgres is running."
}

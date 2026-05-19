$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot/../..

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

if (-not (Get-Command sqlx -ErrorAction SilentlyContinue)) {
  cargo install sqlx-cli --no-default-features --features rustls,postgres
}

sqlx database create
sqlx migrate run --source "infra/migrations"

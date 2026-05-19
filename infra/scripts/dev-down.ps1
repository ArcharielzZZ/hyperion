$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot/../..

$envFile = if (Test-Path ".env") { ".env" } else { ".env.example" }
docker compose -f "infra/docker/docker-compose.yml" --env-file $envFile down

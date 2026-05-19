$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "week1-common.ps1")
Import-HyperionDatabaseEnv

$sql = @"
INSERT INTO scanner_coverage_snapshots (payload)
SELECT to_jsonb(v) FROM v_data_freshness_now v;
"@

Invoke-HyperionSql -Sql $sql
Write-Host "scanner_coverage_snapshots row inserted from v_data_freshness_now"

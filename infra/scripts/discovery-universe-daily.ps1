param(
    # UTC calendar date (yyyy-MM-dd). Defaults to today's UTC date.
    [string]$SnapshotDate = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "week1-common.ps1")
Import-HyperionDatabaseEnv

if (-not $SnapshotDate) {
    $SnapshotDate = [DateTime]::UtcNow.ToString("yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
}
else {
    try {
        $parsed = [DateTime]::Parse(
            $SnapshotDate,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal
        )
        $SnapshotDate = $parsed.ToString("yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
    }
    catch {
        throw "SnapshotDate must be a valid date (e.g. 2026-05-14). Input: $SnapshotDate"
    }
}

$sql = @"
BEGIN;
DELETE FROM discovery_universe_daily WHERE snapshot_date = '$SnapshotDate'::date;
INSERT INTO discovery_universe_daily (
    snapshot_date,
    trader_id,
    wallet,
    promoted,
    discovery_score,
    rank_tier,
    activity_score,
    data_coverage_score,
    latest_behavior_score,
    fills_24h,
    positions_24h,
    pnl_snapshots_24h
)
SELECT
    '$SnapshotDate'::date,
    trader_id,
    wallet,
    promoted,
    discovery_score,
    rank_tier,
    activity_score,
    data_coverage_score,
    latest_behavior_score,
    fills_24h,
    positions_24h,
    pnl_snapshots_24h
FROM trader_discovery_rankings;
COMMIT;
"@

Invoke-HyperionSql -Sql $sql
Write-Host "discovery_universe_daily snapshot complete for $SnapshotDate"

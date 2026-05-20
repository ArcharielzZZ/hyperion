param(
    [string]$OutputDirectory = "",
    [switch]$FailOnCritical,
    [switch]$SkipIngestGaps
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "week1-common.ps1")
Import-HyperionDatabaseEnv

$repo = Get-HyperionRepoRoot
$outDir = if ($OutputDirectory) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
}
else {
    Join-Path $repo "exports\week1\reports"
}
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outPath = Join-Path $outDir "week1_gap_report_$stamp.md"

function Invoke-HyperionSqlScalar {
    param([string]$Sql)
    if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
        throw "psql required for week1-gap-report"
    }
    $raw = & psql $env:DATABASE_URL -v ON_ERROR_STOP=1 -t -A -c $Sql 2>&1
    if ($LASTEXITCODE -ne 0) { throw "psql exited with code $LASTEXITCODE" }
    return ($raw | Where-Object { $_ -ne "" } | Select-Object -First 1)
}

function Get-LagStatus {
    param(
        [string]$Channel,
        $MaxTs,
        $LagSec
    )
    if (-not $MaxTs) { return "missing" }
    # trader-engine recalc defaults to 3600s; scores lag is not an ingest outage.
    if ($Channel -eq "trader_scores") {
        if ($LagSec -gt 7200) { return "critical" }
        if ($LagSec -gt 3900) { return "warn" }
        return "ok"
    }
    if ($LagSec -gt 900) { return "critical" }
    if ($LagSec -gt 300) { return "warn" }
    return "ok"
}

$freshJson = Invoke-HyperionSqlScalar -Sql "SELECT to_jsonb(v) FROM v_data_freshness_now v;"
$fresh = $freshJson | ConvertFrom-Json

$channels = @(
    @{ channel = "fills"; max_ts = $fresh.fills_max_ts; lag_sec = [double]$fresh.fills_lag_sec },
    @{ channel = "trade_ticks"; max_ts = $fresh.trade_ticks_max_ts; lag_sec = [double]$fresh.trade_ticks_lag_sec },
    @{ channel = "positions"; max_ts = $fresh.positions_max_ts; lag_sec = [double]$fresh.positions_lag_sec },
    @{ channel = "pnl_snapshots"; max_ts = $fresh.pnl_snapshots_max_ts; lag_sec = [double]$fresh.pnl_snapshots_lag_sec },
    @{ channel = "market_snapshots"; max_ts = $fresh.market_snapshots_max_ts; lag_sec = [double]$fresh.market_snapshots_lag_sec },
    @{ channel = "trader_scores"; max_ts = $fresh.trader_scores_max_ts; lag_sec = [double]$fresh.trader_scores_lag_sec }
) | ForEach-Object {
    $_ | Add-Member -NotePropertyName status -NotePropertyValue (Get-LagStatus $_.channel $_.max_ts $_.lag_sec) -PassThru
}

$ingestCritical = @($channels | Where-Object {
    $_.channel -ne "trader_scores" -and $_.status -eq "critical"
}).Count

$critical = @($channels | Where-Object { $_.status -eq "critical" }).Count
$warn = @($channels | Where-Object { $_.status -eq "warn" }).Count
$missing = @($channels | Where-Object { $_.status -eq "missing" }).Count

$ingestEvents24h = [int](Invoke-HyperionSqlScalar -Sql "SELECT COUNT(*) FROM ingest_connection_events WHERE occurred_at > NOW() - INTERVAL '24 hours';")
$scannerSnaps24h = [int](Invoke-HyperionSqlScalar -Sql "SELECT COUNT(*) FROM scanner_coverage_snapshots WHERE captured_at > NOW() - INTERVAL '24 hours';")
$latestDiscovery = Invoke-HyperionSqlScalar -Sql "SELECT COALESCE(MAX(snapshot_date)::text, '') FROM discovery_universe_daily;"
$lastWsConnected = Invoke-HyperionSqlScalar -Sql "SELECT COALESCE(MAX(occurred_at)::text, '') FROM ingest_connection_events WHERE event_type = 'ws_connected';"

$ingestGaps24h = 0
$ingestMaxGapSec = 0.0
$gapLines = @("(skipped; omit -SkipIngestGaps for gap detail)")
if (-not $SkipIngestGaps) {
    $ingestGaps24h = [int](Invoke-HyperionSqlScalar -Sql "SELECT COUNT(*) FROM v_week1_ingest_downtime_gaps WHERE gap_start > NOW() - INTERVAL '24 hours';")
    $ingestMaxGapSec = [double](Invoke-HyperionSqlScalar -Sql "SELECT COALESCE(MAX(gap_sec), 0) FROM v_week1_ingest_downtime_gaps WHERE gap_start > NOW() - INTERVAL '24 hours';")
    $gapLines = & psql $env:DATABASE_URL -v ON_ERROR_STOP=1 -A -F "`t" -c @"
SELECT gap_start, gap_end, gap_sec, prior_session_outcome
FROM v_week1_ingest_downtime_gaps
WHERE gap_start > NOW() - INTERVAL '7 days'
ORDER BY gap_start DESC
LIMIT 20;
"@ 2>&1
}

$lines = [System.Collections.Generic.List[string]]::new()
$null = $lines.Add("# Week 1 gap report")
$null = $lines.Add("")
$null = $lines.Add("- Generated (local): $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$null = $lines.Add("- Observed at (DB): $($fresh.observed_at)")
$null = $lines.Add("")
$null = $lines.Add("## Summary")
$null = $lines.Add("")
$null = $lines.Add("| Metric | Value |")
$null = $lines.Add("| --- | --- |")
$null = $lines.Add("| Channels critical | $critical |")
$null = $lines.Add("| Channels warn | $warn |")
$null = $lines.Add("| Channels missing | $missing |")
$null = $lines.Add("| Ingest downtime gaps (24h) | $ingestGaps24h |")
$null = $lines.Add("| Max ingest gap sec (24h) | $([math]::Round($ingestMaxGapSec, 1)) |")
$null = $lines.Add("| Last ws_connected | $lastWsConnected |")
$null = $lines.Add("| Ingest events (24h) | $ingestEvents24h |")
$null = $lines.Add("| Scanner snapshots (24h) | $scannerSnaps24h |")
$null = $lines.Add("| Latest discovery snapshot | $latestDiscovery |")
$null = $lines.Add("")
$null = $lines.Add("## Per-channel health")
$null = $lines.Add("")
$null = $lines.Add("Thresholds: **warn** > 300s lag, **critical** > 900s lag.")
$null = $lines.Add("")
$null = $lines.Add('| Channel | Status | Lag (s) | Max ts |')
$null = $lines.Add('| --- | --- | --- | --- |')
foreach ($ch in ($channels | Sort-Object { switch ($_.status) { 'critical' {1} 'warn' {2} 'missing' {3} default {4} } }, channel)) {
    $null = $lines.Add("| $($ch.channel) | $($ch.status) | $([math]::Round($ch.lag_sec, 1)) | $($ch.max_ts) |")
}
$null = $lines.Add("")
$null = $lines.Add("## Ingest downtime gaps (last 7 days, top 20)")
$null = $lines.Add("")
$null = $lines.Add('```')
foreach ($line in $gapLines) { if ($line) { $null = $lines.Add($line) } }
$null = $lines.Add('```')

$lines -join "`n" | Set-Content -Path $outPath -Encoding utf8
Write-Host "Week 1 gap report written to $outPath"

if ($FailOnCritical -and $ingestCritical -gt 0) {
    throw "Week 1 gap report: $ingestCritical ingest channel(s) in critical state."
}

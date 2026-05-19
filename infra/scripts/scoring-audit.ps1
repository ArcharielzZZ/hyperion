$ErrorActionPreference = "Stop"

# Mirrors services/trader-engine/src/engine.rs scoring gates.
$MinActiveDays = 1
$MinTradeObs = 8
$MinPnlSnapshots = 12

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

$cmd = Get-Command psql -ErrorAction SilentlyContinue
$psql = if ($cmd) { $cmd.Source } else { $null }
if (-not $psql) {
  $candidate = Join-Path $env:USERPROFILE "scoop\apps\postgresql\current\bin\psql.exe"
  if (Test-Path $candidate) { $psql = $candidate }
}
if (-not $psql) {
  throw "psql not found. Install PostgreSQL client or add psql to PATH."
}

# Local calendar "today" for scoop / ingest reporting (created_at window in DB).
$todayLocal = [DateTime]::Today
$tzLocal = [TimeZoneInfo]::Local
$dayStartDto = [DateTimeOffset]::new($todayLocal, $tzLocal.GetUtcOffset($todayLocal))
$dayEndDto = $dayStartDto.AddDays(1)
$dayLabel = $todayLocal.ToString("yyyy-MM-dd")
$tzLabel = $tzLocal.StandardName
$litStart = "'" + $dayStartDto.ToString("yyyy-MM-dd HH:mm:ss.fff", [System.Globalization.CultureInfo]::InvariantCulture) + " " + $dayStartDto.ToString("zzz", [System.Globalization.CultureInfo]::InvariantCulture) + "'"
$litEnd = "'" + $dayEndDto.ToString("yyyy-MM-dd HH:mm:ss.fff", [System.Globalization.CultureInfo]::InvariantCulture) + " " + $dayEndDto.ToString("zzz", [System.Globalization.CultureInfo]::InvariantCulture) + "'"

function Invoke-AuditSql {
  param([string]$Sql)
  $out = & $psql $env:DATABASE_URL -v ON_ERROR_STOP=1 -X -q -t -A -F "`t" -c $Sql 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "psql failed: $out"
  }
  return ($out | Where-Object { $_ -ne $null })
}

$generated = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss' UTC'")
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss")
$outPath = Join-Path "analytics/research" "scoring_audit_$stamp.md"
New-Item -ItemType Directory -Path (Split-Path $outPath) -Force | Out-Null

$lines = [System.Collections.Generic.List[string]]::new()
$null = $lines.Add("# Hyperion scoring audit")
$null = $lines.Add("")
$null = $lines.Add("Generated: **$generated**")
$null = $lines.Add("")
$null = $lines.Add("Eligibility thresholds (trader-engine): active days (30d) >= **$MinActiveDays**, fills+positions (30d) >= **$MinTradeObs**, pnl_snapshots (30d) >= **$MinPnlSnapshots**, ``last_seen`` within 30d, and **promoted** via ``traders.wallet_status = 'PROMOTED'`` after wallet-filter batch (legacy: ``trader_discovery_rankings.promoted`` when filter not run).")
$null = $lines.Add("")
$null = $lines.Add('Wallet filter (Python, interim): min volume **$500**, PnL efficiency **0.05%** of volume, min **5** trades, positive PnL in 7d/1m/3m, HF ban at 10k fills; stage-3 deep dive min **12** snapshots / **60%** completeness.')
$null = $lines.Add("")
$null = $lines.Add('## Scooped today (local calendar)')
$null = $lines.Add("")
$null = $lines.Add("Window: **$dayLabel** ($tzLabel) - rows counted when ``created_at`` falls in the local half-open interval from " + $dayStartDto.ToString("yyyy-MM-dd HH:mm:ss zzz") + " to " + $dayEndDto.ToString("yyyy-MM-dd HH:mm:ss zzz") + " (ingest / DB write time, not exchange event time).")
$null = $lines.Add("")

# --- Today's inventory (created_at)
$invTodaySql = @"
SELECT json_build_object(
  'traders_new_rows', (SELECT COUNT(*) FROM traders WHERE created_at >= $litStart::timestamptz AND created_at < $litEnd::timestamptz),
  'fills_rows', (SELECT COUNT(*) FROM fills WHERE created_at >= $litStart::timestamptz AND created_at < $litEnd::timestamptz),
  'positions_rows', (SELECT COUNT(*) FROM positions WHERE created_at >= $litStart::timestamptz AND created_at < $litEnd::timestamptz),
  'pnl_snapshots_rows', (SELECT COUNT(*) FROM pnl_snapshots WHERE created_at >= $litStart::timestamptz AND created_at < $litEnd::timestamptz),
  'market_snapshots_rows', (SELECT COUNT(*) FROM market_snapshots WHERE created_at >= $litStart::timestamptz AND created_at < $litEnd::timestamptz),
  'trader_scores_rows', (SELECT COUNT(*) FROM trader_scores WHERE created_at >= $litStart::timestamptz AND created_at < $litEnd::timestamptz),
  'behavior_profiles_rows', (SELECT COUNT(*) FROM trader_behavior_profiles WHERE created_at >= $litStart::timestamptz AND created_at < $litEnd::timestamptz),
  'behavioral_alerts_rows', (SELECT COUNT(*) FROM behavioral_alerts WHERE created_at >= $litStart::timestamptz AND created_at < $litEnd::timestamptz),
  'discovery_rankings_rows', (SELECT COUNT(*) FROM trader_discovery_rankings WHERE created_at >= $litStart::timestamptz AND created_at < $litEnd::timestamptz)
)::text;
"@
$invTodayRow = (Invoke-AuditSql $invTodaySql | Select-Object -First 1)
$invToday = $invTodayRow | ConvertFrom-Json

$null = $lines.Add("| Metric (created today) | Count |")
$null = $lines.Add("| --- | ---:|")
foreach ($prop in $invToday.PSObject.Properties.Name | Sort-Object) {
  $null = $lines.Add("| $prop | $($invToday.$prop) |")
}
$null = $lines.Add("")

# --- All-time inventory
$invSql = @"
SELECT json_build_object(
  'traders', (SELECT COUNT(*) FROM traders),
  'fills', (SELECT COUNT(*) FROM fills),
  'fills_30d', (SELECT COUNT(*) FROM fills WHERE timestamp > NOW() - INTERVAL '30 days'),
  'positions', (SELECT COUNT(*) FROM positions),
  'positions_30d', (SELECT COUNT(*) FROM positions WHERE timestamp > NOW() - INTERVAL '30 days'),
  'pnl_snapshots', (SELECT COUNT(*) FROM pnl_snapshots),
  'pnl_snapshots_30d', (SELECT COUNT(*) FROM pnl_snapshots WHERE timestamp > NOW() - INTERVAL '30 days'),
  'trader_scores_rows', (SELECT COUNT(*) FROM trader_scores),
  'behavior_profiles', (SELECT COUNT(*) FROM trader_behavior_profiles),
  'behavioral_alerts', (SELECT COUNT(*) FROM behavioral_alerts),
  'discovery_rankings', (SELECT COUNT(*) FROM trader_discovery_rankings),
  'discovery_promoted', (SELECT COUNT(*) FROM trader_discovery_rankings WHERE promoted = TRUE),
  'market_snapshots', (SELECT COUNT(*) FROM market_snapshots)
)::text;
"@
$invRow = (Invoke-AuditSql $invSql | Select-Object -First 1)
$inv = $invRow | ConvertFrom-Json

$null = $lines.Add("## All-time data inventory")
$null = $lines.Add("")
$null = $lines.Add("| Metric | Count |")
$null = $lines.Add("| --- | ---:|")
foreach ($prop in $inv.PSObject.Properties.Name | Sort-Object) {
  $null = $lines.Add("| $prop | $($inv.$prop) |")
}
$null = $lines.Add("")

# --- Score batches: today first, then all-time footnote
$latestTodaySql = @"
SELECT COALESCE(to_char(MAX(timestamp), 'YYYY-MM-DD HH24:MI:SS TZ'), 'none yet today')
FROM trader_scores
WHERE created_at >= $litStart::timestamptz AND created_at < $litEnd::timestamptz;
"@
$latestTodayTs = (Invoke-AuditSql $latestTodaySql | Select-Object -First 1).Trim()
$latestSql = @"
SELECT COALESCE(to_char(MAX(timestamp), 'YYYY-MM-DD HH24:MI:SS TZ'), 'never') FROM trader_scores;
"@
$latestAllTs = (Invoke-AuditSql $latestSql | Select-Object -First 1).Trim()
$null = $lines.Add("## Score batches persisted")
$null = $lines.Add("")
$null = $lines.Add("**Today ($dayLabel)** - latest ``trader_scores.timestamp`` among rows **inserted today**: **$latestTodayTs**")
$null = $lines.Add("")
$null = $lines.Add("**All-time** (any ``created_at``) latest ``trader_scores.timestamp``: **$latestAllTs**")
$null = $lines.Add("")
$null = $lines.Add('*The Scooped-today score line only moves when ``hyperion-trader-engine`` writes score rows whose ``created_at`` falls in the local-day window above. The all-time line is the latest batch in the table regardless of calendar day.*')
$null = $lines.Add("")

# --- Eligibility funnel
$funnelSql = @"
WITH candidates AS (
  SELECT
    t.id AS trader_id,
    COALESCE((SELECT COUNT(*) FROM fills f WHERE f.trader_id = t.id AND f.timestamp > NOW() - INTERVAL '30 days'), 0) AS fills_30d,
    COALESCE((SELECT COUNT(*) FROM positions p WHERE p.trader_id = t.id AND p.timestamp > NOW() - INTERVAL '30 days'), 0) AS positions_30d,
    COALESCE((SELECT COUNT(*) FROM pnl_snapshots ps WHERE ps.trader_id = t.id AND ps.timestamp > NOW() - INTERVAL '30 days'), 0) AS pnl_snapshots_30d,
    COALESCE((
      SELECT COUNT(DISTINCT DATE(sample_ts))
      FROM (
        SELECT f.timestamp AS sample_ts FROM fills f WHERE f.trader_id = t.id AND f.timestamp > NOW() - INTERVAL '30 days'
        UNION ALL
        SELECT p.timestamp FROM positions p WHERE p.trader_id = t.id AND p.timestamp > NOW() - INTERVAL '30 days'
        UNION ALL
        SELECT ps.timestamp FROM pnl_snapshots ps WHERE ps.trader_id = t.id AND ps.timestamp > NOW() - INTERVAL '30 days'
      ) samples
    ), 0) AS active_days_30d
  FROM traders t
  JOIN trader_discovery_rankings r ON r.trader_id = t.id
  WHERE r.promoted = TRUE AND t.last_seen > NOW() - INTERVAL '30 days'
),
eligible AS (
  SELECT * FROM candidates
  WHERE active_days_30d >= $MinActiveDays
    AND (fills_30d + positions_30d) >= $MinTradeObs
    AND pnl_snapshots_30d >= $MinPnlSnapshots
)
SELECT json_build_object(
  'promoted_recent', (SELECT COUNT(*) FROM candidates),
  'eligible_for_scoring', (SELECT COUNT(*) FROM eligible)
)::text;
"@
$funnel = ((Invoke-AuditSql $funnelSql | Select-Object -First 1) | ConvertFrom-Json)
$null = $lines.Add("## Scoring pipeline funnel (promoted + active)")
$null = $lines.Add("")
$null = $lines.Add("| Stage | Wallets |")
$null = $lines.Add("| --- | ---:|")
$null = $lines.Add("| Promoted discovery traders seen in last 30d | $($funnel.promoted_recent) |")
$null = $lines.Add("| Meet all three data gates (engine candidate set) | $($funnel.eligible_for_scoring) |")
$null = $lines.Add("")
$null = $lines.Add("*Funnel still uses rolling 30d exchange-side activity; it does not filter by today's ingest window.*")
$null = $lines.Add("")

# --- Today's score distribution (rows inserted today only)
$distTodaySql = @"
WITH today_scores AS (
  SELECT *
  FROM trader_scores
  WHERE created_at >= $litStart::timestamptz AND created_at < $litEnd::timestamptz
),
latest AS (
  SELECT DISTINCT ON (trader_id)
    trader_id, total_score, consistency_score, survivability_score, timing_score,
    leverage_discipline_score, conviction_score, style, timestamp
  FROM today_scores
  ORDER BY trader_id, timestamp DESC
)
SELECT json_build_object(
  'wallets_with_score_from_today_batch', (SELECT COUNT(*) FROM latest),
  'total_p50', (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY total_score) FROM latest),
  'total_p90', (SELECT percentile_cont(0.9) WITHIN GROUP (ORDER BY total_score) FROM latest),
  'total_min', (SELECT MIN(total_score) FROM latest),
  'total_max', (SELECT MAX(total_score) FROM latest),
  'total_avg', (SELECT AVG(total_score) FROM latest)
)::text;
"@
$distToday = ((Invoke-AuditSql $distTodaySql | Select-Object -First 1) | ConvertFrom-Json)
$null = $lines.Add('## Scooped today - score distribution')
$null = $lines.Add("")
$null = $lines.Add("Latest row per wallet among **only** ``trader_scores`` rows **inserted today** (``created_at`` in local day window).")
$null = $lines.Add("")
$null = $lines.Add("| Stat | Value |")
$null = $lines.Add("| --- | ---:|")
foreach ($p in @('wallets_with_score_from_today_batch','total_min','total_p50','total_avg','total_p90','total_max')) {
  $v = $distToday.$p
  if ($null -eq $v) { $v = "(empty)" }
  elseif ($v -is [double] -or $v -is [decimal]) { $v = [math]::Round([double]$v, 4) }
  $null = $lines.Add("| $p | $v |")
}
$null = $lines.Add("")

# --- Today's style mix
$styleTodaySql = @"
WITH today_scores AS (
  SELECT * FROM trader_scores
  WHERE created_at >= $litStart::timestamptz AND created_at < $litEnd::timestamptz
),
latest AS (
  SELECT DISTINCT ON (trader_id) style
  FROM today_scores
  ORDER BY trader_id, timestamp DESC
)
SELECT style || chr(9) || COUNT(*)::text FROM latest GROUP BY style ORDER BY COUNT(*) DESC;
"@
$null = $lines.Add('## Scooped today - style mix')
$null = $lines.Add("")
$null = $lines.Add("| Style | Wallets |")
$null = $lines.Add("| --- | ---:|")
$styleTodayRows = @(Invoke-AuditSql $styleTodaySql)
if ($styleTodayRows.Count -eq 0) {
  $null = $lines.Add("| *no score rows inserted today* | |")
} else {
  foreach ($row in $styleTodayRows) {
    $parts = $row -split "`t", 2
    if ($parts.Length -eq 2) { $null = $lines.Add("| $($parts[0]) | $($parts[1]) |") }
  }
}
$null = $lines.Add("")

# --- Top wallets today
$topTodaySql = @"
WITH today_scores AS (
  SELECT * FROM trader_scores
  WHERE created_at >= $litStart::timestamptz AND created_at < $litEnd::timestamptz
),
latest AS (
  SELECT DISTINCT ON (trader_id)
    trader_id, total_score, consistency_score, survivability_score, timing_score,
    leverage_discipline_score, conviction_score, style, timestamp
  FROM today_scores
  ORDER BY trader_id, timestamp DESC
)
SELECT
  t.wallet || chr(9) ||
  ROUND(l.total_score::numeric, 2) || chr(9) ||
  ROUND(l.consistency_score::numeric, 2) || chr(9) ||
  ROUND(l.survivability_score::numeric, 2) || chr(9) ||
  ROUND(l.timing_score::numeric, 2) || chr(9) ||
  ROUND(l.leverage_discipline_score::numeric, 2) || chr(9) ||
  ROUND(l.conviction_score::numeric, 2) || chr(9) ||
  l.style
FROM latest l
JOIN traders t ON t.id = l.trader_id
ORDER BY l.total_score DESC NULLS LAST
LIMIT 40;
"@
$null = $lines.Add('## Scooped today - top 40 (by total score, rows inserted today only)')
$null = $lines.Add("")
$null = $lines.Add("| Wallet | Total | Consist. | Surviv. | Timing | Lev. disc. | Convict. | Style |")
$null = $lines.Add("| --- | ---:| ---:| ---:| ---:| ---:| ---:| --- |")
$topTodayRows = @(Invoke-AuditSql $topTodaySql)
if ($topTodayRows.Count -eq 0) {
  $null = $lines.Add("| *no score rows inserted today* | | | | | | | |")
} else {
  foreach ($row in $topTodayRows) {
    $p = $row -split "`t"
    if ($p.Length -ge 8) {
      $w = $p[0]
      if ($w.Length -gt 14) { $w = $w.Substring(0, 6) + "..." + $w.Substring($w.Length - 6) }
      $null = $lines.Add("| ``$w`` | $($p[1]) | $($p[2]) | $($p[3]) | $($p[4]) | $($p[5]) | $($p[6]) | $($p[7]) |")
    }
  }
}
$null = $lines.Add("")

# --- Latest score distribution (all-time latest per wallet)
$distSql = @"
WITH latest AS (
  SELECT DISTINCT ON (trader_id)
    trader_id, total_score, consistency_score, survivability_score, timing_score,
    leverage_discipline_score, conviction_score, style, timestamp
  FROM trader_scores
  ORDER BY trader_id, timestamp DESC
)
SELECT json_build_object(
  'wallets_with_latest_score', COUNT(*),
  'total_p50', percentile_cont(0.5) WITHIN GROUP (ORDER BY total_score),
  'total_p90', percentile_cont(0.9) WITHIN GROUP (ORDER BY total_score),
  'total_min', MIN(total_score),
  'total_max', MAX(total_score),
  'total_avg', AVG(total_score)
)::text FROM latest;
"@
$dist = ((Invoke-AuditSql $distSql | Select-Object -First 1) | ConvertFrom-Json)
$null = $lines.Add("## All-time view - latest score distribution (one row per trader)")
$null = $lines.Add("")
$null = $lines.Add("| Stat | Value |")
$null = $lines.Add("| --- | ---:|")
foreach ($p in @('wallets_with_latest_score','total_min','total_p50','total_avg','total_p90','total_max')) {
  $v = $dist.$p
  if ($v -is [double] -or $v -is [decimal]) { $v = [math]::Round([double]$v, 4) }
  $null = $lines.Add("| $p | $v |")
}
$null = $lines.Add("")

# --- Style mix
$styleSql = @"
WITH latest AS (
  SELECT DISTINCT ON (trader_id) style
  FROM trader_scores
  ORDER BY trader_id, timestamp DESC
)
SELECT style || chr(9) || COUNT(*)::text FROM latest GROUP BY style ORDER BY COUNT(*) DESC;
"@
$null = $lines.Add("## All-time view - style mix (latest score per trader)")
$null = $lines.Add("")
$null = $lines.Add("| Style | Wallets |")
$null = $lines.Add("| --- | ---:|")
foreach ($row in (Invoke-AuditSql $styleSql)) {
  $parts = $row -split "`t", 2
  if ($parts.Length -eq 2) { $null = $lines.Add("| $($parts[0]) | $($parts[1]) |") }
}
$null = $lines.Add("")

# --- Top wallets
$topSql = @"
WITH latest AS (
  SELECT DISTINCT ON (trader_id)
    trader_id, total_score, consistency_score, survivability_score, timing_score,
    leverage_discipline_score, conviction_score, style, timestamp
  FROM trader_scores
  ORDER BY trader_id, timestamp DESC
)
SELECT
  t.wallet || chr(9) ||
  ROUND(l.total_score::numeric, 2) || chr(9) ||
  ROUND(l.consistency_score::numeric, 2) || chr(9) ||
  ROUND(l.survivability_score::numeric, 2) || chr(9) ||
  ROUND(l.timing_score::numeric, 2) || chr(9) ||
  ROUND(l.leverage_discipline_score::numeric, 2) || chr(9) ||
  ROUND(l.conviction_score::numeric, 2) || chr(9) ||
  l.style
FROM latest l
JOIN traders t ON t.id = l.trader_id
ORDER BY l.total_score DESC NULLS LAST
LIMIT 40;
"@
$null = $lines.Add("## All-time view - top 40 by latest total score")
$null = $lines.Add("")
$null = $lines.Add("| Wallet | Total | Consist. | Surviv. | Timing | Lev. disc. | Convict. | Style |")
$null = $lines.Add("| --- | ---:| ---:| ---:| ---:| ---:| ---:| --- |")
foreach ($row in (Invoke-AuditSql $topSql)) {
  $p = $row -split "`t"
  if ($p.Length -ge 8) {
    $w = $p[0]
    if ($w.Length -gt 14) { $w = $w.Substring(0, 6) + "..." + $w.Substring($w.Length - 6) }
    $null = $lines.Add("| ``$w`` | $($p[1]) | $($p[2]) | $($p[3]) | $($p[4]) | $($p[5]) | $($p[6]) | $($p[7]) |")
  }
}
$null = $lines.Add("")

# --- Discovery tiers (promoted)
$tierSql = @"
SELECT rank_tier || chr(9) || COUNT(*)::text
FROM trader_discovery_rankings
WHERE promoted = TRUE
GROUP BY rank_tier
ORDER BY COUNT(*) DESC;
"@
$null = $lines.Add("## Discovery rank tiers (promoted only)")
$null = $lines.Add("")
$null = $lines.Add("| Tier | Wallets |")
$null = $lines.Add("| --- | ---:|")
foreach ($row in (Invoke-AuditSql $tierSql)) {
  $parts = $row -split "`t", 2
  if ($parts.Length -eq 2) { $null = $lines.Add("| $($parts[0]) | $($parts[1]) |") }
}
$null = $lines.Add("")

# --- Alerts today (local calendar, detected_at)
$alertTodaySql = @"
SELECT COALESCE(severity, '') || chr(9) || COALESCE(alert_type, '') || chr(9) || COUNT(*)::text
FROM behavioral_alerts
WHERE detected_at >= $litStart::timestamptz AND detected_at < $litEnd::timestamptz
GROUP BY severity, alert_type
ORDER BY COUNT(*) DESC
LIMIT 25;
"@
$null = $lines.Add("## Behavioral alerts (today, local calendar)")
$null = $lines.Add("")
$null = $lines.Add("| Severity | Type | Count |")
$null = $lines.Add("| --- | --- | ---:|")
$alertTodayRows = @(Invoke-AuditSql $alertTodaySql)
if ($alertTodayRows.Count -eq 0) {
  $null = $lines.Add("| *none today* | | |")
} else {
  foreach ($row in $alertTodayRows) {
    $p = $row -split "`t"
    if ($p.Length -ge 3) { $null = $lines.Add("| $($p[0]) | $($p[1]) | $($p[2]) |") }
  }
}
$null = $lines.Add("")

# --- Alerts 14d
$alertSql = @"
SELECT COALESCE(severity, '') || chr(9) || COALESCE(alert_type, '') || chr(9) || COUNT(*)::text
FROM behavioral_alerts
WHERE detected_at > NOW() - INTERVAL '14 days'
GROUP BY severity, alert_type
ORDER BY COUNT(*) DESC
LIMIT 25;
"@
$null = $lines.Add("## Behavioral alerts (last 14 days, top 25 type/severity pairs)")
$null = $lines.Add("")
$null = $lines.Add("| Severity | Type | Count |")
$null = $lines.Add("| --- | --- | ---:|")
$alertRows = @(Invoke-AuditSql $alertSql)
if ($alertRows.Count -eq 0) {
  $null = $lines.Add("| *none* | | |")
} else {
  foreach ($row in $alertRows) {
    $p = $row -split "`t"
    if ($p.Length -ge 3) { $null = $lines.Add("| $($p[0]) | $($p[1]) | $($p[2]) |") }
  }
}
$null = $lines.Add("")

# --- Gap: eligible but never scored
$gapSql = @"
WITH candidates AS (
  SELECT t.id AS trader_id
  FROM traders t
  JOIN trader_discovery_rankings r ON r.trader_id = t.id
  WHERE r.promoted = TRUE AND t.last_seen > NOW() - INTERVAL '30 days'
),
metrics AS (
  SELECT
    c.trader_id,
    COALESCE((SELECT COUNT(*) FROM fills f WHERE f.trader_id = c.trader_id AND f.timestamp > NOW() - INTERVAL '30 days'), 0) AS fills_30d,
    COALESCE((SELECT COUNT(*) FROM positions p WHERE p.trader_id = c.trader_id AND p.timestamp > NOW() - INTERVAL '30 days'), 0) AS positions_30d,
    COALESCE((SELECT COUNT(*) FROM pnl_snapshots ps WHERE ps.trader_id = c.trader_id AND ps.timestamp > NOW() - INTERVAL '30 days'), 0) AS pnl_snapshots_30d,
    COALESCE((
      SELECT COUNT(DISTINCT DATE(sample_ts))
      FROM (
        SELECT f.timestamp AS sample_ts FROM fills f WHERE f.trader_id = c.trader_id AND f.timestamp > NOW() - INTERVAL '30 days'
        UNION ALL
        SELECT p.timestamp FROM positions p WHERE p.trader_id = c.trader_id AND p.timestamp > NOW() - INTERVAL '30 days'
        UNION ALL
        SELECT ps.timestamp FROM pnl_snapshots ps WHERE ps.trader_id = c.trader_id AND ps.timestamp > NOW() - INTERVAL '30 days'
      ) samples
    ), 0) AS active_days_30d
  FROM candidates c
),
eligible AS (
  SELECT trader_id FROM metrics
  WHERE active_days_30d >= $MinActiveDays
    AND (fills_30d + positions_30d) >= $MinTradeObs
    AND pnl_snapshots_30d >= $MinPnlSnapshots
)
SELECT COUNT(*)::text FROM eligible e
LEFT JOIN trader_scores s ON s.trader_id = e.trader_id
WHERE s.id IS NULL;
"@
$gap = (Invoke-AuditSql $gapSql | Select-Object -First 1).Trim()
$null = $lines.Add("## Pipeline gap")
$null = $lines.Add("")
$null = $lines.Add("Eligible traders (by SQL) with **no** ``trader_scores`` row at all: **$gap** (if non-zero, run ``hyperion-trader-engine`` at least once after data accrues).")
$null = $lines.Add("")

$copyabilitySql = @"
SELECT json_build_object(
  'rows_total', (SELECT COUNT(*) FROM trader_copyability_advisory),
  'wallets_recorded', (SELECT COUNT(DISTINCT wallet) FROM trader_copyability_advisory),
  'latest_snapshot', (SELECT MAX(computed_at)::text FROM trader_copyability_advisory)
)::text;
"@
$copyability = ((Invoke-AuditSql $copyabilitySql | Select-Object -First 1) | ConvertFrom-Json)
$null = $lines.Add("## Advisory copyability inventory")
$null = $lines.Add("")
$null = $lines.Add("| Field | Value |")
$null = $lines.Add("| --- | --- |")
foreach ($p in @('rows_total','wallets_recorded','latest_snapshot')) {
  $v = $copyability.$p
  if ($null -eq $v) { $v = "(empty)" }
  $null = $lines.Add("| $p | $v |")
}
$null = $lines.Add("")
$null = $lines.Add("*Rows come from manual ``hyperion-pipeline compute-copyability-advisory``. They do not gate promotion logic.*")
$null = $lines.Add("")

$null = $lines.Add("---")
$null = $lines.Add("")
$null = $lines.Add('*Regenerate: ``powershell -ExecutionPolicy Bypass -File infra/scripts/scoring-audit.ps1`` from the ``hyperion`` directory.*')

$content = $lines -join "`r`n"
Set-Content -Path $outPath -Value $content -Encoding UTF8
Write-Host "Wrote $outPath"

<#
.SYNOPSIS
  Deep backfill Hyperliquid fills (userFillsByTime + time-split) for every promoted wallet.

.DESCRIPTION
  Uses the same window as manual Sept-2025 runs: start ms = 2025-09-01 00:00 UTC by default,
  end ms = UTC now at script start (shared for all wallets for a consistent batch).
  Wallets = UNION(promoted discovery traders, HYPERLIQUID_TRACKED_USERS from ../.env), deduped.
  Ordered by current fill count ascending so very heavy wallets run last.

.PARAMETER StartMs
  Unix epoch milliseconds inclusive lower bound (default: 1756684800000 = 2025-09-01T00:00:00Z).

.PARAMETER PostgresHost
  Default 127.0.0.1 (psql inventory query only).

.PARAMETER Database
  Default hyperion.

#>
param(
    [long]$StartMs = 1756684800000L,
    [string]$PostgresHost = "127.0.0.1",
    [string]$Database = "hyperion",
    [string]$PostgresUser = "postgres"
)

$ErrorActionPreference = "Continue"
$HyperionRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$PythonDir = Join-Path $HyperionRoot "python"
$EnvFile = Join-Path $HyperionRoot ".env"
$Psql = Join-Path $env:USERPROFILE "scoop\apps\postgresql\current\bin\psql.exe"
if (-not (Test-Path $Psql)) {
    $Psql = "psql"
}

$endMs = [int64]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$outCsv = Join-Path $HyperionRoot "analytics\research\deep_backfill_promoted_$ts.csv"

$candidates = @()
$sql = @"
SELECT lower(t.wallet) AS w
FROM trader_discovery_rankings r
JOIN traders t ON t.id = r.trader_id
LEFT JOIN fills f ON f.trader_id = t.id
WHERE r.promoted = TRUE
  AND lower(t.wallet) <> '0x0000000000000000000000000000000000000000'
GROUP BY t.wallet
ORDER BY COUNT(f.id) ASC NULLS FIRST;
"@
$rows = & $Psql -h $PostgresHost -U $PostgresUser -d $Database -t -A -c $sql
foreach ($line in $rows) {
    $w = $line.Trim()
    if ($w) { $candidates += $w }
}

if (Test-Path $EnvFile) {
    $m = Select-String -Path $EnvFile -Pattern '^\s*HYPERLIQUID_TRACKED_USERS\s*=\s*(.+)\s*$' | Select-Object -First 1
    if ($m) {
        $raw = $m.Matches[0].Groups[1].Value.Trim()
        foreach ($part in $raw.Split(",")) {
            $u = $part.Trim().ToLowerInvariant()
            if ($u.StartsWith("0x") -and $u.Length -ge 42) {
                $candidates += $u
            }
        }
    }
}

$wallets = $candidates | Select-Object -Unique

$exeCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\Scripts\hyperion-pipeline.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\Scripts\hyperion-pipeline.exe")
)
$exe = $exeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $exe) {
    throw "hyperion-pipeline.exe not found under LocalAppData Python Scripts; pip install -e hyperion/python first."
}

$rowsOut = New-Object System.Collections.Generic.List[object]
Push-Location $PythonDir
try {
    $i = 0
    foreach ($w in $wallets) {
        $i++
        Write-Host "[$i/$($wallets.Count)] backfill-wallet $w (start=$StartMs end=$endMs)"
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $attempted = $null
        $inserted = $null
        $exit = 0
        $log = ""
        try {
            $prevEap = $ErrorActionPreference
            $ErrorActionPreference = "SilentlyContinue"
            $log = & $exe backfill-wallet $w --start-ms $StartMs --end-ms $endMs --to-postgres --parquet 2>&1 | Out-String
            $ErrorActionPreference = $prevEap
            if ($null -ne $LASTEXITCODE) { $exit = $LASTEXITCODE }
            else { $exit = 0 }
        }
        catch {
            $log = $_.Exception.Message
            $exit = 1
        }
        $sw.Stop()
        if ($log -match 'fills_attempted=(\d+)\s+fills_inserted=(\d+)') {
            $attempted = [int64]$Matches[1]
            $inserted = [int64]$Matches[2]
        }
        [void]$rowsOut.Add([pscustomobject]@{
                wallet            = $w
                fills_attempted   = $attempted
                fills_inserted    = $inserted
                exit_code         = $exit
                duration_sec      = [math]::Round($sw.Elapsed.TotalSeconds, 2)
                batch_start_ms    = $StartMs
                batch_end_ms      = $endMs
            })
        if ($exit -ne 0) {
            Write-Warning "Exit $exit for $w - tail log:`n$($log.Substring([Math]::Max(0, $log.Length - 800)))"
        }
        Start-Sleep -Milliseconds 300
    }

    $rowsOut | Export-Csv -Path $outCsv -NoTypeInformation -Encoding UTF8
    Write-Host "Wrote $outCsv"

    Write-Host @"
Next (rerank): from hyperion repo root run:
  `$env:TRADER_ENGINE_BIND_ADDR='127.0.0.1:18082'; `$env:RUST_LOG='info'; cargo run -p hyperion-trader-engine
Stop the process after logs show 'trader-engine listening' if the default port is already in use elsewhere.
"@
}
finally {
    Pop-Location
}

$sumA = ($rowsOut | Measure-Object -Property fills_attempted -Sum).Sum
$sumI = ($rowsOut | Measure-Object -Property fills_inserted -Sum).Sum
Write-Host "TOTAL fills_attempted=$sumA fills_inserted=$sumI wallets=$($wallets.Count)"

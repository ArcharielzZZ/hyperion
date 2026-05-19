#!/usr/bin/env python3
"""
Discover Hyperliquid whale wallets (all-time volume + hit rate) from the public leaderboard
and optional portfolio API enrichment. Writes research report + CSV and can upsert Postgres.

Usage (from hyperion/):
  python infra/scripts/discover-hyperliquid-whales.py --apply-db
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO_URL = "https://api.hyperliquid.xyz/info"


@dataclass(slots=True)
class WhaleCandidate:
    wallet: str
    account_value_usd: float
    all_time_vlm_usd: float
    all_time_pnl_usd: float
    all_time_roi: float
    month_vlm_usd: float
    month_roi: float
    hit_rate_pct: float | None
    pnl_periods: int
    rank_by_vlm: int
    whale_tier: str
    promote: bool


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_env_database_url() -> str | None:
    root = _repo_root()
    env_path = root / ".env"
    if not env_path.exists():
        env_path = root / ".env.example"
    if not env_path.exists():
        return os.environ.get("DATABASE_URL")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    return os.environ.get("DATABASE_URL")


def _http_post_json(url: str, body: dict[str, Any], timeout: float = 60.0) -> Any:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json(url: str, timeout: float = 120.0) -> Any:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _window(row: dict[str, Any], name: str) -> dict[str, str] | None:
    for entry in row.get("windowPerformances", []):
        if isinstance(entry, list) and len(entry) == 2 and entry[0] == name:
            return entry[1]
    return None


def _f(x: str | float | int | None, default: float = 0.0) -> float:
    if x is None:
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def hit_rate_from_pnl_history(pnl_history: list[list[Any]]) -> tuple[float | None, int]:
    if len(pnl_history) < 3:
        return None, 0
    ordered = sorted(pnl_history, key=lambda p: int(p[0]))
    deltas: list[float] = []
    for i in range(1, len(ordered)):
        deltas.append(_f(ordered[i][1]) - _f(ordered[i - 1][1]))
    if len(deltas) < 5:
        return None, len(deltas)
    wins = sum(1 for d in deltas if d > 0)
    return 100.0 * wins / len(deltas), len(deltas)


def fetch_portfolio_hit_rate(wallet: str) -> tuple[float | None, int]:
    try:
        payload = _http_post_json(INFO_URL, {"type": "portfolio", "user": wallet.lower()})
    except Exception:
        return None, 0
    if not isinstance(payload, list):
        return None, 0
    for entry in payload:
        if isinstance(entry, list) and len(entry) == 2 and entry[0] == "perpAllTime":
            block = entry[1]
            if isinstance(block, dict) and "pnlHistory" in block:
                return hit_rate_from_pnl_history(block["pnlHistory"])
    for entry in payload:
        if isinstance(entry, list) and len(entry) == 2 and entry[0] == "allTime":
            block = entry[1]
            if isinstance(block, dict) and "pnlHistory" in block:
                return hit_rate_from_pnl_history(block["pnlHistory"])
    return None, 0


def classify_tier(vlm: float, hit: float | None, roi: float) -> str:
    if vlm >= 1_000_000_000:
        return "mega"
    if vlm >= 100_000_000:
        return "whale"
    if vlm >= 10_000_000:
        return "large"
    return "active"


def should_promote(
    vlm: float,
    hit: float | None,
    roi: float,
    min_vlm: float,
    min_hit: float,
    *,
    elite: bool,
) -> bool:
    if vlm < min_vlm or roi <= 0:
        return False
    if elite:
        # Noise filter: require proven positive ROI and either strong hit rate or mega volume.
        if hit is not None and hit >= min_hit:
            return True
        return vlm >= 100_000_000 and roi >= 0.10
    if hit is not None and hit >= min_hit:
        return True
    if vlm >= 50_000_000 and roi >= 0.08:
        return True
    if vlm >= 100_000_000 and roi > 0:
        return True
    return False


def parse_leaderboard(
    data: dict[str, Any],
    *,
    min_all_time_vlm: float,
    min_account_value: float,
    enrich_top_n: int,
    min_hit_rate: float,
    portfolio_delay_sec: float,
    elite: bool,
) -> list[WhaleCandidate]:
    rows = data.get("leaderboardRows", [])
    parsed: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        w = str(row.get("ethAddress", "")).lower()
        if not w.startswith("0x") or len(w) < 42:
            continue
        all_time = _window(row, "allTime")
        if not all_time:
            continue
        vlm = _f(all_time.get("vlm"))
        if vlm < min_all_time_vlm:
            continue
        av = _f(row.get("accountValue"))
        if av < min_account_value:
            continue
        parsed.append(
            (
                int(vlm),
                {
                    "wallet": w,
                    "account_value_usd": av,
                    "all_time_vlm_usd": vlm,
                    "all_time_pnl_usd": _f(all_time.get("pnl")),
                    "all_time_roi": _f(all_time.get("roi")),
                    "month_vlm_usd": _f((_window(row, "month") or {}).get("vlm")),
                    "month_roi": _f((_window(row, "month") or {}).get("roi")),
                },
            )
        )

    parsed.sort(key=lambda x: x[0], reverse=True)
    out: list[WhaleCandidate] = []
    for rank, (_, item) in enumerate(parsed, start=1):
        wallet = item["wallet"]
        hit: float | None = None
        periods = 0
        if rank <= enrich_top_n:
            hit, periods = fetch_portfolio_hit_rate(wallet)
            if portfolio_delay_sec > 0:
                time.sleep(portfolio_delay_sec)
        tier = classify_tier(item["all_time_vlm_usd"], hit, item["all_time_roi"])
        promote = should_promote(
            item["all_time_vlm_usd"],
            hit,
            item["all_time_roi"],
            min_all_time_vlm,
            min_hit_rate,
            elite=elite,
        )
        out.append(
            WhaleCandidate(
                wallet=wallet,
                account_value_usd=item["account_value_usd"],
                all_time_vlm_usd=item["all_time_vlm_usd"],
                all_time_pnl_usd=item["all_time_pnl_usd"],
                all_time_roi=item["all_time_roi"],
                month_vlm_usd=item["month_vlm_usd"],
                month_roi=item["month_roi"],
                hit_rate_pct=hit,
                pnl_periods=periods,
                rank_by_vlm=rank,
                whale_tier=tier,
                promote=promote,
            )
        )
    return out


def write_outputs(candidates: list[WhaleCandidate], *, min_vlm: float, min_hit: float) -> tuple[Path, Path]:
    root = _repo_root()
    research = root / "analytics" / "research"
    research.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = research / f"hyperliquid_whale_candidates_{stamp}.csv"
    md_path = research / f"hyperliquid_whale_discovery_{stamp}.md"

    promoted = [c for c in candidates if c.promote]
    with_hit = [c for c in candidates if c.hit_rate_pct is not None]

    lines = [
        "# Hyperliquid whale discovery (L1 leaderboard)",
        "",
        f"Generated: **{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC**",
        "",
        "## Method",
        "",
        "- Source: official Hyperliquid mainnet leaderboard (`stats-data.hyperliquid.xyz`).",
        "- **All-time volume (`vlm`)** identifies whales; **portfolio `perpAllTime` PnL history**",
        "  (top ranks by volume) estimates **hit rate** as % of positive PnL step changes.",
        "- Local `trade_ticks` only spans ~10 days in this environment; leaderboard covers",
        "  **full Hyperliquid perp history** per wallet.",
        "",
        "## Filters applied",
        "",
        f"- Minimum all-time volume: **${min_vlm:,.0f}**",
        f"- Minimum hit rate (when enriched): **{min_hit:.1f}%** with positive all-time ROI",
        f"- Fallback promote: volume ≥ $50M and ROI ≥ 8%, or volume ≥ $100M and ROI > 0",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"| --- | ---: |",
        f"| Leaderboard rows passing volume filter | {len(candidates)} |",
        f"| Portfolio hit rate enriched | {len(with_hit)} |",
        f"| **Recommended for promotion (`whale` tier)** | **{len(promoted)}** |",
        "",
        "## Top 25 by all-time volume (promotion candidates)",
        "",
        "| Rank | Wallet | All-time vol (USD) | All-time ROI | Hit rate % | Tier | Promote |",
        "| ---: | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for c in candidates[:25]:
        hit_s = f"{c.hit_rate_pct:.1f}" if c.hit_rate_pct is not None else "—"
        lines.append(
            f"| {c.rank_by_vlm} | `{c.wallet}` | {c.all_time_vlm_usd:,.0f} | "
            f"{c.all_time_roi * 100:.2f}% | {hit_s} | {c.whale_tier} | {'yes' if c.promote else 'no'} |"
        )

    lines.extend(
        [
            "",
            "## Top 25 by hit rate (volume ≥ $10M, enriched only)",
            "",
            "| Wallet | Hit rate % | All-time vol (USD) | All-time ROI | Promote |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    by_hit = sorted(
        [c for c in with_hit if c.all_time_vlm_usd >= 10_000_000],
        key=lambda c: c.hit_rate_pct or 0,
        reverse=True,
    )[:25]
    for c in by_hit:
        lines.append(
            f"| `{c.wallet}` | {c.hit_rate_pct:.1f} | {c.all_time_vlm_usd:,.0f} | "
            f"{c.all_time_roi * 100:.2f}% | {'yes' if c.promote else 'no'} |"
        )

    lines.extend(
        [
            "",
            "## Integration",
            "",
            "- Run with `--apply-db` to set `trader_discovery_rankings.promoted = true` and",
            "  `rank_tier = 'whale'` for recommended wallets.",
            "- Add promoted wallets to `HYPERLIQUID_TRACKED_USERS` for private user-channel ingest.",
            "- Run `deep-backfill-promoted-wallets.ps1` for historical fills.",
            "",
            f"CSV: `{csv_path.name}`",
        ]
    )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    header = (
        "rank,wallet,account_value_usd,all_time_vlm_usd,all_time_pnl_usd,all_time_roi,"
        "month_vlm_usd,month_roi,hit_rate_pct,pnl_periods,whale_tier,promote\n"
    )
    body = "".join(
        f"{c.rank_by_vlm},{c.wallet},{c.account_value_usd:.2f},{c.all_time_vlm_usd:.2f},"
        f"{c.all_time_pnl_usd:.2f},{c.all_time_roi:.6f},{c.month_vlm_usd:.2f},{c.month_roi:.6f},"
        f"{'' if c.hit_rate_pct is None else f'{c.hit_rate_pct:.2f}'},{c.pnl_periods},"
        f"{c.whale_tier},{int(c.promote)}\n"
        for c in candidates
    )
    csv_path.write_text(header + body, encoding="utf-8")
    return csv_path, md_path


def cap_promotions(candidates: list[WhaleCandidate], max_promote: int) -> list[WhaleCandidate]:
    import math

    promotable = [c for c in candidates if c.promote]
    if max_promote <= 0 or len(promotable) <= max_promote:
        return candidates

    def score(c: WhaleCandidate) -> float:
        hit = c.hit_rate_pct if c.hit_rate_pct is not None else 50.0
        return hit * math.log10(max(c.all_time_vlm_usd, 1.0))

    keep = {c.wallet for c in sorted(promotable, key=score, reverse=True)[:max_promote]}
    out: list[WhaleCandidate] = []
    for c in candidates:
        if c.promote and c.wallet not in keep:
            out.append(
                WhaleCandidate(
                    wallet=c.wallet,
                    account_value_usd=c.account_value_usd,
                    all_time_vlm_usd=c.all_time_vlm_usd,
                    all_time_pnl_usd=c.all_time_pnl_usd,
                    all_time_roi=c.all_time_roi,
                    month_vlm_usd=c.month_vlm_usd,
                    month_roi=c.month_roi,
                    hit_rate_pct=c.hit_rate_pct,
                    pnl_periods=c.pnl_periods,
                    rank_by_vlm=c.rank_by_vlm,
                    whale_tier=c.whale_tier,
                    promote=False,
                )
            )
        else:
            out.append(c)
    return out


def apply_to_registry(candidates: list[WhaleCandidate], database_url: str) -> None:
    try:
        import psycopg
    except ImportError as e:
        raise SystemExit("psycopg required for --apply-db: pip install psycopg[binary]") from e

    promote = [c for c in candidates if c.promote]
    if not promote:
        print("No wallets to register.", file=sys.stderr)
        return

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM hyperliquid_whale_registry")
            for c in promote:
                cur.execute(
                    """
                    INSERT INTO hyperliquid_whale_registry (
                        wallet, all_time_vlm_usd, all_time_roi, hit_rate_pct, whale_tier, source, notes
                    ) VALUES (%s, %s, %s, %s, %s, 'leaderboard_discovery', %s)
                    ON CONFLICT (wallet) DO UPDATE SET
                        all_time_vlm_usd = EXCLUDED.all_time_vlm_usd,
                        all_time_roi = EXCLUDED.all_time_roi,
                        hit_rate_pct = EXCLUDED.hit_rate_pct,
                        whale_tier = EXCLUDED.whale_tier,
                        discovered_at = NOW()
                    """,
                    (
                        c.wallet.lower(),
                        c.all_time_vlm_usd,
                        c.all_time_roi,
                        c.hit_rate_pct,
                        c.whale_tier,
                        f"rank_vlm={c.rank_by_vlm}",
                    ),
                )
        conn.commit()
    print(f"Registered {len(promote)} wallets in hyperliquid_whale_registry.", file=sys.stderr)


def apply_to_database(candidates: list[WhaleCandidate], database_url: str) -> None:
    try:
        import psycopg
    except ImportError as e:
        raise SystemExit("psycopg required for --apply-db: pip install psycopg[binary]") from e

    promote_wallets = [c.wallet.lower() for c in candidates if c.promote]
    if not promote_wallets:
        print("No wallets to promote.", file=sys.stderr)
        return

    now = datetime.now(timezone.utc)
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for wallet in promote_wallets:
                cur.execute(
                    """
                    INSERT INTO traders (id, wallet, first_seen, last_seen, created_at)
                    VALUES (gen_random_uuid(), %s, %s, %s, NOW())
                    ON CONFLICT (wallet) DO UPDATE SET last_seen = GREATEST(traders.last_seen, EXCLUDED.last_seen)
                    RETURNING id
                    """,
                    (wallet, now, now),
                )
                trader_id = cur.fetchone()[0]
                c = next(x for x in candidates if x.wallet == wallet)
                cur.execute(
                    """
                    INSERT INTO trader_discovery_rankings (
                        id, trader_id, wallet,
                        public_trade_count_1h, public_trade_count_24h,
                        public_notional_usd_1h, public_notional_usd_24h,
                        active_coins_24h, buy_ratio_24h,
                        fills_24h, positions_24h, pnl_snapshots_24h,
                        last_public_trade_at, latest_behavior_score,
                        data_coverage_score, activity_score, discovery_score,
                        rank_tier, promoted, timestamp
                    ) VALUES (
                        gen_random_uuid(), %s, %s,
                        0, 0, 0, 0, 0, 0.5,
                        0, 0, 0,
                        NULL, 0,
                        90, 95, 92,
                        'whale', TRUE, %s
                    )
                    ON CONFLICT (trader_id) DO UPDATE SET
                        promoted = TRUE,
                        rank_tier = 'whale',
                        activity_score = GREATEST(trader_discovery_rankings.activity_score, EXCLUDED.activity_score),
                        discovery_score = GREATEST(trader_discovery_rankings.discovery_score, EXCLUDED.discovery_score),
                        timestamp = EXCLUDED.timestamp
                    """,
                    (trader_id, wallet, now),
                )
        conn.commit()
    print(f"Applied whale promotion for {len(promote_wallets)} wallets.", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover Hyperliquid whale wallets from leaderboard.")
    parser.add_argument("--leaderboard-cache", type=Path, help="Optional local leaderboard JSON path")
    parser.add_argument("--min-all-time-vlm", type=float, default=10_000_000.0, help="USD all-time perp volume")
    parser.add_argument("--min-account-value", type=float, default=50_000.0, help="USD account value floor")
    parser.add_argument("--min-hit-rate", type=float, default=52.0, help="Min hit rate %% when enriched")
    parser.add_argument("--enrich-top-n", type=int, default=150, help="Portfolio API calls for top N by volume")
    parser.add_argument("--portfolio-delay", type=float, default=0.15, help="Seconds between portfolio requests")
    parser.add_argument("--apply-db", action="store_true", help="Promote whales in trader_discovery_rankings")
    parser.add_argument(
        "--elite",
        action="store_true",
        help="Stricter promotion (hit rate or $100M+ vol with ROI >= 10%%); demotes prior whale tier first",
    )
    parser.add_argument(
        "--demote-existing-whales",
        action="store_true",
        help="Set rank_tier != whale back to promoted=false before apply (use with --elite)",
    )
    parser.add_argument(
        "--max-promote",
        type=int,
        default=0,
        help="Cap promoted wallets to top N by (hit_rate * log10(vlm)) among those passing filters (0 = no cap)",
    )
    args = parser.parse_args()

    print("Downloading Hyperliquid leaderboard...", file=sys.stderr)
    if args.leaderboard_cache and args.leaderboard_cache.exists():
        data = json.loads(args.leaderboard_cache.read_text(encoding="utf-8"))
    else:
        data = _http_get_json(LEADERBOARD_URL, timeout=180.0)
        cache = _repo_root() / "analytics" / "research" / "hyperliquid_leaderboard_cache.json"
        cache.write_text(json.dumps(data), encoding="utf-8")
        print(f"Cached leaderboard to {cache}", file=sys.stderr)

    if args.demote_existing_whales or args.elite:
        db_url = _load_env_database_url()
        if db_url and args.apply_db:
            try:
                import psycopg

                with psycopg.connect(db_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE trader_discovery_rankings
                            SET promoted = FALSE, rank_tier = 'watchlist'
                            WHERE rank_tier = 'whale'
                            """
                        )
                    conn.commit()
                print("Demoted previous whale-tier rows.", file=sys.stderr)
            except ImportError:
                pass

    candidates = parse_leaderboard(
        data,
        min_all_time_vlm=args.min_all_time_vlm,
        min_account_value=args.min_account_value,
        enrich_top_n=args.enrich_top_n,
        min_hit_rate=args.min_hit_rate,
        portfolio_delay_sec=args.portfolio_delay,
        elite=args.elite,
    )
    if args.max_promote > 0:
        candidates = cap_promotions(candidates, args.max_promote)
    csv_path, md_path = write_outputs(candidates, min_vlm=args.min_all_time_vlm, min_hit=args.min_hit_rate)
    print(f"Wrote {csv_path}", file=sys.stderr)
    print(f"Wrote {md_path}", file=sys.stderr)
    print(f"Candidates: {len(candidates)}; promote: {sum(1 for c in candidates if c.promote)}", file=sys.stderr)

    if args.apply_db:
        db_url = _load_env_database_url()
        if not db_url:
            raise SystemExit("DATABASE_URL not set")
        apply_to_registry(candidates, db_url)
        apply_to_database(candidates, db_url)


if __name__ == "__main__":
    main()

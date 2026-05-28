"""
Hyperion: Twitter (X) Force Puller
==================================
Pulls all tweets of a given handle within a date range via Playwright
network-interception (the `UserTweets` GraphQL endpoint). Stores them as
Parquet for the dashboard and updates `history.json` + a live
`.pull_status.json` so the dashboard can show progress.

Persistent Chrome profile lives under
``analytics/data_lake/twitter/.x_profile/`` so the user only logs in once.

CLI
---
    python analytics/scripts/force_pull_twitter.py \
        --handle Mark_Nr1 \
        --from 2025-01-01 \
        --to   2026-05-23
    optional: --headless  (only works after first manual login)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from analytics.lib.parquet_io import atomic_write_parquet  # noqa: E402

TWITTER_DIR = REPO_ROOT / "analytics" / "data_lake" / "twitter"
PROFILE_DIR = TWITTER_DIR / ".x_profile"
HISTORY_PATH = TWITTER_DIR / "history.json"
STATUS_PATH = TWITTER_DIR / ".pull_status.json"

USERTW_ENDPOINT = "UserTweets"


# --------------------------------------------------------------------------- #
# Status / history helpers (atomic writes so Dash never reads half-files)
# --------------------------------------------------------------------------- #

def _atomic_write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_status(**fields) -> None:
    base = {
        "state": "running",
        "handle": "",
        "from": "",
        "to": "",
        "found": 0,
        "last_date": "",
        "message": "",
        "error": "",
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if STATUS_PATH.exists():
        try:
            base.update(json.loads(STATUS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    base.update(fields)
    base["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _atomic_write_json(STATUS_PATH, base)


def load_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(history: list[dict]) -> None:
    _atomic_write_json(HISTORY_PATH, history)


def upsert_history(entry: dict) -> None:
    """Replace any earlier entry for the same handle+from+to triple."""
    history = load_history()
    key = (entry["handle"], entry["from"], entry["to"])
    history = [
        h for h in history
        if (h.get("handle"), h.get("from"), h.get("to")) != key
    ]
    history.append(entry)
    save_history(history)


# --------------------------------------------------------------------------- #
# GraphQL parsing
# --------------------------------------------------------------------------- #

def _parse_x_date(s: str) -> datetime | None:
    """Parse 'Wed Oct 05 12:38:18 +0000 2024' into a UTC datetime."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc)
    except ValueError:
        return None


def _extract_media_urls(tweet_result: dict) -> list[str]:
    """Pull image + video preview URLs out of a tweet_result.legacy."""
    urls: list[str] = []
    legacy = tweet_result.get("legacy", {}) or {}

    media_lists = []
    entities = legacy.get("entities") or {}
    if entities.get("media"):
        media_lists.append(entities["media"])
    ext = legacy.get("extended_entities") or {}
    if ext.get("media"):
        media_lists.append(ext["media"])

    for media in media_lists:
        for m in media:
            kind = m.get("type", "")
            if kind == "photo":
                u = m.get("media_url_https")
                if u and u not in urls:
                    urls.append(u)
            else:
                preview = m.get("media_url_https")
                if preview and preview not in urls:
                    urls.append(preview)
                variants = (m.get("video_info") or {}).get("variants") or []
                mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
                if mp4s:
                    mp4s.sort(key=lambda v: v.get("bitrate") or 0, reverse=True)
                    best = mp4s[0].get("url")
                    if best and best not in urls:
                        urls.append(best)
    return urls


def parse_usertw_response(data: dict) -> list[dict]:
    """Adapted from x_network_watch.py - also pulls media URLs."""
    results: list[dict] = []
    try:
        user_result = data["data"]["user"]["result"]
        tl_root = user_result.get("timeline_v2") or user_result.get("timeline")
        instructions = tl_root["timeline"]["instructions"]
    except (KeyError, TypeError):
        return results

    for instr in instructions:
        entries = instr.get("entries", [])
        for entry in entries:
            entry_id = entry.get("entryId", "")
            is_pinned = entry_id.startswith("promoted-tweet-") or "pinned" in entry_id

            try:
                item_content = entry["content"]["itemContent"]
            except (KeyError, TypeError):
                continue

            tweet_result = item_content.get("tweet_results", {}).get("result", {})
            if not tweet_result:
                continue
            if tweet_result.get("__typename") == "TweetWithVisibilityResults":
                tweet_result = tweet_result.get("tweet", tweet_result)

            legacy = tweet_result.get("legacy", {}) or {}
            user_legacy = (
                tweet_result.get("core", {})
                .get("user_results", {})
                .get("result", {})
                .get("legacy", {})
            )
            screen_name = user_legacy.get("screen_name", "")
            tweet_id = legacy.get("id_str", "")
            full_text = legacy.get("full_text", "")
            created_at_raw = legacy.get("created_at", "")
            created_dt = _parse_x_date(created_at_raw)

            url = f"https://x.com/{screen_name}/status/{tweet_id}" if tweet_id else ""
            media_urls = _extract_media_urls(tweet_result)

            if not tweet_id:
                continue

            results.append({
                "id": tweet_id,
                "handle": screen_name,
                "text": full_text,
                "url": url,
                "created_at": created_dt,
                "created_at_raw": created_at_raw,
                "media_urls": media_urls,
                "pinned": is_pinned,
            })
    return results


# --------------------------------------------------------------------------- #
# Playwright
# --------------------------------------------------------------------------- #

def launch_context(pw, headless: bool):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        headless=headless,
        channel="chrome",
        ignore_default_args=["--enable-automation"],
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
        viewport={"width": 1280, "height": 900},
    )


# --------------------------------------------------------------------------- #
# Main scrape loop
# --------------------------------------------------------------------------- #

def scrape(
    handle: str,
    from_date: datetime,
    to_date: datetime,
    headless: bool,
    max_scrolls: int = 400,
) -> list[dict]:
    handle = handle.lstrip("@").strip()
    collected: dict[str, dict] = {}

    write_status(
        state="running",
        handle=handle,
        **{"from": from_date.date().isoformat(), "to": to_date.date().isoformat()},
        found=0,
        last_date="",
        message="Browser starten ...",
        error="",
    )

    def on_response(response):
        if USERTW_ENDPOINT not in response.url:
            return
        try:
            body = response.json()
        except Exception:
            return
        try:
            tweets = parse_usertw_response(body)
        except Exception:
            return
        new_added = False
        for t in tweets:
            if not t["id"]:
                continue
            if t["id"] not in collected:
                collected[t["id"]] = t
                new_added = True

        if new_added:
            non_pinned = [t for t in collected.values() if not t["pinned"] and t["created_at"]]
            last_date = ""
            if non_pinned:
                oldest = min(non_pinned, key=lambda t: t["created_at"])
                last_date = oldest["created_at"].date().isoformat()
            write_status(
                state="running",
                handle=handle,
                **{"from": from_date.date().isoformat(), "to": to_date.date().isoformat()},
                found=len(collected),
                last_date=last_date,
                message=f"Sammle Tweets ... ({len(collected)})",
            )

    with sync_playwright() as pw:
        ctx = launch_context(pw, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("response", on_response)

        write_status(message="Login pruefen ...")
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(2_500)

        login_deadline = time.time() + 180
        while time.time() < login_deadline:
            cur = page.url
            if cur.startswith("https://x.com/home") or cur.startswith("https://twitter.com/home"):
                break
            write_status(message=f"Warte auf Login (URL: {cur[:60]}) ...")
            page.wait_for_timeout(2_000)

        write_status(message=f"Oeffne Profil @{handle} ...")
        try:
            page.goto(
                f"https://x.com/{handle}",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(4_000)

        scrolls = 0
        idle_rounds = 0
        last_count = 0

        while scrolls < max_scrolls:
            non_pinned = [t for t in collected.values() if not t["pinned"] and t["created_at"]]
            if non_pinned:
                oldest = min(non_pinned, key=lambda t: t["created_at"])
                if oldest["created_at"] < from_date:
                    write_status(message=f"Fertig - aelteste Tweets erreicht ({oldest['created_at'].date()}).")
                    break

            try:
                page.mouse.wheel(0, 12_000)
            except Exception:
                pass
            page.wait_for_timeout(2_200)
            scrolls += 1

            if len(collected) == last_count:
                idle_rounds += 1
            else:
                idle_rounds = 0
            last_count = len(collected)

            if idle_rounds >= 6:
                write_status(message="Keine neuen Tweets mehr - Ende der Timeline.")
                break

        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass

    return list(collected.values())


# --------------------------------------------------------------------------- #
# Save
# --------------------------------------------------------------------------- #

def save_pull(
    handle: str,
    from_date: datetime,
    to_date: datetime,
    tweets: list[dict],
) -> tuple[Path, int]:
    """Filter tweets to date range, write parquet, return (path, count)."""
    in_range = [
        t for t in tweets
        if t["created_at"] is not None
        and from_date <= t["created_at"] <= to_date
    ]
    in_range.sort(key=lambda t: t["created_at"])

    TWITTER_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{handle.lower()}__{from_date.date().isoformat()}__{to_date.date().isoformat()}"
    out_path = TWITTER_DIR / f"{stem}.parquet"

    if not in_range:
        df = pl.DataFrame(
            schema={
                "id": pl.Utf8,
                "handle": pl.Utf8,
                "text": pl.Utf8,
                "url": pl.Utf8,
                "created_at": pl.Datetime("ms", time_zone="UTC"),
                "media_urls": pl.List(pl.Utf8),
            }
        )
    else:
        df = pl.DataFrame({
            "id": [t["id"] for t in in_range],
            "handle": [t["handle"] for t in in_range],
            "text": [t["text"] for t in in_range],
            "url": [t["url"] for t in in_range],
            "created_at": [t["created_at"] for t in in_range],
            "media_urls": [t["media_urls"] for t in in_range],
        }).with_columns(pl.col("created_at").cast(pl.Datetime("ms", time_zone="UTC")))

    atomic_write_parquet(df, out_path, compression="zstd")
    return out_path, len(in_range)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _parse_date(s: str, *, end: bool) -> datetime:
    dt = datetime.strptime(s, "%Y-%m-%d")
    if end:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Force pull tweets of a single handle in a date range.")
    parser.add_argument("--handle", required=True, help="X handle without @")
    parser.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--headless", action="store_true", help="Run headless (only after first manual login)")
    args = parser.parse_args(argv)

    try:
        from_dt = _parse_date(args.date_from, end=False)
        to_dt = _parse_date(args.date_to, end=True)
    except ValueError as exc:
        print(f"Bad date: {exc}")
        write_status(state="error", error=f"Bad date: {exc}", message="Abbruch.")
        return 2

    if from_dt > to_dt:
        msg = f"--from ({from_dt.date()}) liegt nach --to ({to_dt.date()})."
        print(msg)
        write_status(state="error", error=msg, message="Abbruch.")
        return 2

    try:
        tweets = scrape(args.handle, from_dt, to_dt, headless=args.headless)
    except Exception as exc:
        write_status(state="error", error=str(exc), message="Fehler im Scraper.")
        traceback.print_exc()
        return 1

    out_path, count = save_pull(args.handle, from_dt, to_dt, tweets)

    entry = {
        "handle": args.handle.lstrip("@").strip(),
        "from": from_dt.date().isoformat(),
        "to": to_dt.date().isoformat(),
        "count": count,
        "parquet": str(out_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "visible": True,
        "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    upsert_history(entry)

    write_status(
        state="done",
        handle=entry["handle"],
        **{"from": entry["from"], "to": entry["to"]},
        found=count,
        message=f"Fertig - {count} Tweets gespeichert.",
        error="",
    )

    print(f"OK - {count} Tweets -> {out_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())

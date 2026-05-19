"""Map Hyperliquid user-fill payloads to ``TradeFill`` rows (same keys as Rust ingest)."""

from __future__ import annotations

from datetime import datetime, timezone

from hyperion_pipeline.models.pydantic_domain import TradeFill


class FillNormalizer:
    """
    ``event_key`` matches Rust ``services/ingest/src/validation.rs`` userFills branch:

    ``fill|{wallet}|{coin}|{hash}|{tid}|{oid}``

    Side is normalized like ``RawSide::as_normalized``: B → buy, A → sell.
    """

    @staticmethod
    def _ms_to_utc(ms: int) -> datetime:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)

    @staticmethod
    def _normalize_side(raw: str) -> str:
        s = raw.strip().upper()
        if s == "B":
            return "buy"
        if s == "A":
            return "sell"
        return raw.lower()

    @classmethod
    def _one_fill(cls, wallet_lower: str, f: dict) -> TradeFill:
        coin = str(f["coin"])
        side = cls._normalize_side(str(f["side"]))
        px = f["px"]
        sz = f["sz"]
        price = float(px) if not isinstance(px, (int, float)) else float(px)
        size = float(sz) if not isinstance(sz, (int, float)) else float(sz)
        ts = int(f["time"])
        dt = cls._ms_to_utc(ts)
        h = str(f["hash"])
        tid = int(f["tid"])
        oid = int(f["oid"])
        event_key = f"fill|{wallet_lower}|{coin}|{h}|{tid}|{oid}"
        lev_raw = f.get("leverage")
        lev = float(lev_raw) if lev_raw is not None and lev_raw != "" else 0.0
        fp = f.get("closedPnl") or f.get("closed_pnl")
        fec = f.get("fee")
        cpv = float(fp) if fp not in (None, "") else None
        feev = float(fec) if fec not in (None, "") else None
        dire = str(f["dir"]).strip() if f.get("dir") is not None else None

        raw = {"hash": h, "tid": tid, "oid": oid, "dir": dire}
        if cpv is not None:
            raw["closed_pnl"] = cpv
        if feev is not None:
            raw["fee"] = feev
        return TradeFill(
            wallet=wallet_lower,
            coin=coin,
            side=side,
            size=size,
            leverage=lev,
            price=price,
            event_timestamp=dt,
            event_key=event_key,
            fill_dir=dire,
            closed_pnl_usd=cpv,
            fee_usd=feev,
            raw=raw,
        )

    @classmethod
    def from_fill_dicts(cls, wallet: str, fills_raw: list[dict]) -> list[TradeFill]:
        """REST ``userFills`` / ``userFillsByTime`` returns a bare array of fill objects."""

        w = wallet.lower()
        return [cls._one_fill(w, f) for f in fills_raw]

    @classmethod
    def from_user_fills_snapshot(cls, user: str, payload: dict) -> list[TradeFill]:
        """WebSocket-style ``{"user":..., "fills":[...]}`` (or wrapped in ``data``)."""

        data = payload.get("data") or payload
        fills_raw = data.get("fills") or []
        wallet = (data.get("user") or user).lower()
        return [cls._one_fill(wallet, f) for f in fills_raw]

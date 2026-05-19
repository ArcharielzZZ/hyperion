"""Pluggable adverse-selection slippage model for deterministic paper replay."""


from __future__ import annotations

from abc import ABC, abstractmethod


class SlippageModel(ABC):
    """Abstract adverse slippage transform for replay pricing."""

    @abstractmethod
    def adjust_leg_px(self, *, is_buy_leg: bool, mid_px: float) -> float:
        """Return the worse-fill price assuming buy pays up / sell receives less."""

    @property
    @abstractmethod
    def identifier(self) -> str:
        """Human-readable slug for Markdown output."""


class LinearBpsSlippage(SlippageModel):
    """
    Purpose: deterministic linear bips widening per synthetic leg.

    Inputs: absolute slippage in basis points applied symmetrically vs mid.
    Output: adversely shifted limit price toward the liquidity taker side.

    Gotcha: ignores size, liquidity, and coin microstructure — only a sanity bound.
    """

    def __init__(self, slippage_bps: float) -> None:
        self._bps = float(slippage_bps)

    @property
    def identifier(self) -> str:
        return f"linear_{self._bps:.2f}_bps_per_leg"

    def adjust_leg_px(self, *, is_buy_leg: bool, mid_px: float) -> float:
        widen = max(0.0, self._bps) / 10_000.0
        if is_buy_leg:
            return float(mid_px) * (1.0 + widen)
        return float(mid_px) * (1.0 - widen)


def apply_slippage_for_long_round_trip(
    *,
    entry_buy_px_mid: float,
    exit_sell_px_mid: float,
    model: SlippageModel,
) -> tuple[float, float]:
    """Long: buy entry worse up, exit sell receives less."""

    entry = model.adjust_leg_px(is_buy_leg=True, mid_px=entry_buy_px_mid)
    exit_px = model.adjust_leg_px(is_buy_leg=False, mid_px=exit_sell_px_mid)
    return entry, exit_px


def apply_slippage_for_short_round_trip(
    *,
    entry_sell_px_mid: float,
    exit_buy_px_mid: float,
    model: SlippageModel,
) -> tuple[float, float]:
    """Short: sell entry worse down (receive less vs mid), exit buy pays up."""

    entry = model.adjust_leg_px(is_buy_leg=False, mid_px=entry_sell_px_mid)
    exit_px = model.adjust_leg_px(is_buy_leg=True, mid_px=exit_buy_px_mid)
    return entry, exit_px

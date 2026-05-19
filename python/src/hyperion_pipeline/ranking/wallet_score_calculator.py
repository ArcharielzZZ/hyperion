"""Normalization + weighted sum with optional per-factor caps."""

from __future__ import annotations

from typing import Literal

import math

from hyperion_pipeline.ranking.config_schema import RankingConfigYaml


class WalletScoreCalculator:
    """Turns raw factor vectors into a single score in ``[0, 1]`` (post-weighted scale)."""

    def __init__(self, config: RankingConfigYaml) -> None:
        self._config = config

    @staticmethod
    def _minmax(values: list[float]) -> list[float]:
        if not values:
            return []
        lo, hi = min(values), max(values)
        if math.isclose(lo, hi):
            return [0.5 for _ in values]
        return [(v - lo) / (hi - lo) for v in values]

    @staticmethod
    def _zscore(values: list[float]) -> list[float]:
        if not values:
            return []
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / max(len(values) - 1, 1)
        std = math.sqrt(var)
        if std < 1e-12:
            return [0.5 for _ in values]
        zs = [(v - mean) / std for v in values]
        lo, hi = min(zs), max(zs)
        if math.isclose(lo, hi):
            return [0.5 for _ in zs]
        return [(z - lo) / (hi - lo) for z in zs]

    def normalize_matrix(
        self,
        factor_names: list[str],
        raw_matrix: list[list[float]],
        *,
        mode: Literal["minmax", "zscore", "none"] | None = None,
    ) -> list[list[float]]:
        """Each inner list is one wallet's raw vector aligned with ``factor_names``."""

        m = mode or self._config.normalization
        if m == "none":
            return [list(row) for row in raw_matrix]
        cols: list[list[float]] = [[] for _ in factor_names]
        for row in raw_matrix:
            for i, v in enumerate(row):
                cols[i].append(float(v))
        norm_cols: list[list[float]] = []
        for col in cols:
            if m == "minmax":
                norm_cols.append(self._minmax(col))
            else:
                norm_cols.append(self._zscore(col))
        out: list[list[float]] = []
        for wi in range(len(raw_matrix)):
            out.append([norm_cols[fi][wi] for fi in range(len(factor_names))])
        return out

    def weighted_score(self, factor_names: list[str], normalized: list[float]) -> float:
        """Apply weights + caps; returns weighted sum (not forced to 1)."""

        total = 0.0
        for name, val in zip(factor_names, normalized, strict=True):
            w = self._config.weights[name]
            cap = self._config.factor_caps.get(name, 1.0)
            v = min(float(val), cap)
            total += w * v
        return float(total)

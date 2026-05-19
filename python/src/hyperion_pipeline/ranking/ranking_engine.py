"""Batch ranking orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field

from hyperion_pipeline.ranking.config_schema import RankingConfigYaml
from hyperion_pipeline.ranking.factor_registry import FactorRegistry
from hyperion_pipeline.ranking.wallet_score_calculator import WalletScoreCalculator


@dataclass(slots=True)
class RankingEngine:
    """
    Modular scoring engine: YAML weights + pluggable registry + normalization.

    ``raw_by_wallet`` maps wallet -> factor_name -> raw value (pre-registered factors only).
    """

    config: RankingConfigYaml
    registry: FactorRegistry
    _calc: WalletScoreCalculator = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._calc = WalletScoreCalculator(self.config)

    def rank_wallets(self, raw_by_wallet: dict[str, dict[str, float]]) -> list[tuple[str, float]]:
        names = list(self.config.weights.keys())
        wallets = list(raw_by_wallet.keys())
        raw_matrix: list[list[float]] = []
        for w in wallets:
            vec = self.registry.compute_wallet_vector(names, raw_by_wallet[w])
            raw_matrix.append([vec[n] for n in names])
        norm_matrix = self._calc.normalize_matrix(names, raw_matrix)
        scores: list[tuple[str, float]] = []
        for wallet, norm_vec in zip(wallets, norm_matrix, strict=True):
            scores.append((wallet, self._calc.weighted_score(names, norm_vec)))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

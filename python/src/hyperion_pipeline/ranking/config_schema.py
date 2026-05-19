"""YAML / JSON ranking configuration (validated with Pydantic)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RankingConfigYaml(BaseModel):
    """Schema for ``example_configs/ranking.example.yaml``."""

    model_config = ConfigDict(extra="forbid")

    version: str
    decay_half_life_days: float = Field(default=30.0, ge=0.0)
    normalization: Literal["minmax", "zscore", "none"] = "minmax"
    weights: dict[str, float]
    factor_caps: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> RankingConfigYaml:
        total = sum(self.weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1.0, got {total}")
        return self

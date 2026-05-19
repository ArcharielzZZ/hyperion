from pathlib import Path

import yaml

from hyperion_pipeline.ranking.config_schema import RankingConfigYaml
from hyperion_pipeline.ranking.factor_registry import FactorRegistry
from hyperion_pipeline.ranking.ranking_engine import RankingEngine


def test_ranking_engine_minmax():
    python_dir = Path(__file__).resolve().parents[1]
    cfg_path = python_dir / "example_configs" / "ranking.example.yaml"
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    rc = RankingConfigYaml.model_validate(raw)
    engine = RankingEngine(rc, FactorRegistry())
    keys = list(rc.weights)
    raw_by = {
        "0xlow": {k: 0.0 for k in keys},
        "0xhigh": {k: 1.0 for k in keys},
    }
    ranked = engine.rank_wallets(raw_by)
    assert ranked[0][0] == "0xhigh"

"""Process-wide dependency wiring (override in tests)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from hyperion_pipeline.config.settings import Settings, get_settings
from hyperion_pipeline.ingestion.fill_storage_engine import FillStorageEngine


@dataclass
class Container:
    """Lightweight DI container."""

    settings: Settings

    @classmethod
    def from_default_settings(cls) -> Container:
        return cls(settings=get_settings())

    @cached_property
    def fill_storage(self) -> FillStorageEngine:
        return FillStorageEngine(self.settings.pipeline_data_dir)

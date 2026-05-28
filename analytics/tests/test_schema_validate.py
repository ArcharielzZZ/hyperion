"""Tests for schema contract validation."""

from __future__ import annotations

import polars as pl
import pytest

from analytics.lib.schema_validate import assert_dataframe_columns
from analytics.lib.schemas import FILLS_SCHEMA, ORDERS_SCHEMA


def test_assert_dataframe_columns_ok():
    df = pl.DataFrame(schema=FILLS_SCHEMA)
    assert_dataframe_columns(df, FILLS_SCHEMA, label="fills")


def test_assert_dataframe_columns_missing_raises():
    df = pl.DataFrame({"timestamp": pl.Series([], dtype=pl.Datetime("ms"))})
    with pytest.raises(ValueError, match="missing columns"):
        assert_dataframe_columns(df, ORDERS_SCHEMA, label="orders")

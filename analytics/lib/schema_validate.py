"""Validate Polars DataFrames against shared schema contracts."""

from __future__ import annotations

import polars as pl


def assert_dataframe_columns(
    df: pl.DataFrame,
    schema: dict[str, pl.DataType],
    *,
    label: str,
) -> None:
    """Raise ValueError if required schema columns are missing."""
    expected = set(schema.keys())
    actual = set(df.columns)
    missing = expected - actual
    if missing:
        raise ValueError(
            f"{label}: missing columns {sorted(missing)} "
            f"(have {sorted(actual)}, expected {sorted(expected)})"
        )

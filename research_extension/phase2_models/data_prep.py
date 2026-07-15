"""
AWH Phase 2 — shared column-preparation helper for Isolation Forest models
and the supervised classifier. All models consume the same relative-to-
baseline stat columns (see build_benchmark_dataset.py::compute_windows for
why absolute values were replaced with baseline-relative ones). Any NaN
(insufficient trailing baseline history, or a feature entirely dropped out
within a window) falls back to that column's own training-set mean —
missing_frac already carries the "how much was actually missing" signal
explicitly, so a neutral fallback for the rest is safe.
"""

from __future__ import annotations

import pandas as pd

from build_benchmark_dataset import FEATURE_COLUMNS

STAT_SUFFIXES = ["rel_mean", "rel_std", "rel_min", "rel_max", "rel_slope", "missing_frac", "max_run_frac"]


def stat_columns(feature: str) -> list[str]:
    return [f"{feature}_{s}" for s in STAT_SUFFIXES]


def all_stat_columns() -> list[str]:
    return [c for feature in FEATURE_COLUMNS for c in stat_columns(feature)]


def prepare_columns(
    df: pd.DataFrame,
    columns: list[str],
    fill_values: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    x = df[columns].copy()
    if fill_values is None:
        fill_values = {c: float(x[c].mean()) for c in columns}
    for c in columns:
        x[c] = x[c].fillna(fill_values[c])
    return x, fill_values

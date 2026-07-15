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

# Detection columns only — rel_slope excluded. rel_slope is genuinely informative
# for drift (its defining property is a ramp), but for fault types with no ramp
# shape (dropout, stuck_at) it's just ordinary sensor noise — a 7th noisy
# dimension fed into a per-feature IsolationForest that measurably hurt those
# fault types' detection recall (confirmed by ablation: reverting to 6 columns
# for detection restored round-1 recall almost exactly). Same "too many
# dimensions for one forest" problem already diagnosed for the two-stage
# model's 70-column joint detector, just at a smaller scale.
DETECTION_STAT_SUFFIXES = ["rel_mean", "rel_std", "rel_min", "rel_max", "missing_frac", "max_run_frac"]
# Attribution columns — adds rel_slope back in, used only to rank candidate
# causal parameters among windows a detector has already flagged anomalous,
# where it demonstrably helps (drift attribution-given-detection: 61% -> 86%).
ATTRIBUTION_STAT_SUFFIXES = DETECTION_STAT_SUFFIXES + ["rel_slope"]

STAT_SUFFIXES = ATTRIBUTION_STAT_SUFFIXES  # kept for callers that want the full set (e.g. the joint detector, the supervised classifier)


def stat_columns(feature: str, suffixes: list[str] = STAT_SUFFIXES) -> list[str]:
    return [f"{feature}_{s}" for s in suffixes]


def detection_columns(feature: str) -> list[str]:
    return stat_columns(feature, DETECTION_STAT_SUFFIXES)


def attribution_columns(feature: str) -> list[str]:
    return stat_columns(feature, ATTRIBUTION_STAT_SUFFIXES)


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

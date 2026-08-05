"""
AWH Synthetic Fault Injection — Phase 2: Benchmark Dataset

Injects labeled synthetic sensor faults into real per-station time series.
Since only the targeted column is perturbed, that column is the unambiguous
ground-truth causal parameter for every window it overlaps — this is what
makes RQ1 attribution accuracy measurable without manual expert labeling.

Four fault types, each confined to a single feature column over a randomized
time span:
  spike     — short, large-magnitude deviation
  drift     — gradual linear shift away from the true value
  stuck_at  — value freezes at its pre-fault reading (sensor lockup)
  dropout   — value goes null (connection loss / read failure)

Usage (library — see build_benchmark_dataset.py for the orchestrating script):
    from inject_synthetic_faults import inject_faults, FAULT_TYPES
    df, fault_log = inject_faults(df, station_id, feature_columns, rng)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FAULT_TYPES = ["spike", "drift", "stuck_at", "dropout"]

# Duration ranges per fault type, in minutes
DURATION_RANGES = {
    "spike":    (5, 15),
    "drift":    (60, 240),
    "stuck_at": (15, 120),
    "dropout":  (10, 60),
}

# Spike/drift magnitude expressed as a multiple of the column's own std dev
SPIKE_MAGNITUDE_STD = (4.0, 8.0)
DRIFT_MAGNITUDE_STD = (2.0, 5.0)


def _duration_minutes(fault_type: str, rng: np.random.Generator) -> int:
    lo, hi = DURATION_RANGES[fault_type]
    return int(rng.integers(lo, hi + 1))


def _apply_spike(series: pd.Series, mask: pd.Series, magnitude: float) -> pd.Series:
    out = series.copy()
    direction = 1 if magnitude >= 0 else -1
    out.loc[mask] = out.loc[mask] + direction * abs(magnitude)
    return out


def _apply_drift(series: pd.Series, mask: pd.Series, magnitude: float) -> pd.Series:
    out = series.copy()
    idx = np.flatnonzero(mask.to_numpy())
    if idx.size == 0:
        return out
    ramp = np.linspace(0.0, magnitude, num=idx.size)
    out.iloc[idx] = out.iloc[idx].to_numpy() + ramp
    return out


def _apply_stuck_at(series: pd.Series, mask: pd.Series) -> pd.Series:
    out = series.copy()
    idx = np.flatnonzero(mask.to_numpy())
    if idx.size == 0:
        return out
    frozen_value = out.iloc[idx[0]]
    out.iloc[idx] = frozen_value
    return out


def _apply_dropout(series: pd.Series, mask: pd.Series) -> pd.Series:
    out = series.copy()
    out.loc[mask] = np.nan
    return out


def inject_faults(
    df: pd.DataFrame,
    station_id: int,
    feature_columns: list[str],
    rng: np.random.Generator,
    n_faults: int,
    time_col: str = "time",
    dead_periods: list[dict] | None = None,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Inject `n_faults` randomly-placed, randomly-typed faults into `df`
    (one station's raw readings, sorted by `time_col`), one feature column
    per fault. Returns the perturbed dataframe and an audit log of every
    injected fault (station_id, start, end, parameter, fault_type, magnitude).

    `dead_periods` (see build_benchmark_dataset.py::detect_dead_periods) are
    real, undocumented stretches where a feature's sensor is frozen/dead —
    found 2026-07-29, PENDING_TASKS.md Round 10. Injecting a synthetic fault
    on top of an already-dead feature is undetectable by construction
    (compute_windows's rolling baseline has zero variance there, so the
    relative-stat columns collapse to a fixed fallback regardless of the
    fault's magnitude) — placement avoids these exactly like it avoids
    overlapping with another already-placed synthetic fault.
    """
    out = df.sort_values(time_col).reset_index(drop=True).copy()
    fault_log: list[dict] = []
    dead_periods = dead_periods or []

    n_rows = len(out)
    if n_rows < 10:
        return out, fault_log

    # Track placed intervals station-wide (not per-column) so that no window
    # ever has two competing real perturbations — a window with overlapping
    # faults has an inherently ambiguous "causal_parameter" ground truth.
    placed_intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    max_placement_attempts = 20

    for _ in range(n_faults):
        fault_type = rng.choice(FAULT_TYPES)
        column = rng.choice(feature_columns)
        col_series = out[column]
        column_dead_periods = [d for d in dead_periods if d["parameter"] == column]

        valid = col_series.notna()
        if valid.sum() < 5:
            continue

        duration_min = _duration_minutes(fault_type, rng)

        start_time = end_time = mask = None
        for _attempt in range(max_placement_attempts):
            start_idx = int(rng.integers(0, n_rows - 1))
            candidate_start = out.loc[start_idx, time_col]
            candidate_end = candidate_start + pd.Timedelta(minutes=duration_min)

            overlaps = any(
                candidate_start < placed_end and placed_start < candidate_end
                for placed_start, placed_end in placed_intervals
            )
            overlaps_dead = any(
                candidate_start < d["end"] and d["start"] < candidate_end
                for d in column_dead_periods
            )
            if overlaps or overlaps_dead:
                continue

            candidate_mask = (out[time_col] >= candidate_start) & (out[time_col] < candidate_end)
            if candidate_mask.sum() < 2:
                continue

            start_time, end_time, mask = candidate_start, candidate_end, candidate_mask
            break

        if start_time is None:
            continue  # no non-overlapping placement found after max attempts — skip this fault

        placed_intervals.append((start_time, end_time))

        col_std = col_series.std(skipna=True)
        col_std = col_std if col_std and col_std > 0 else abs(col_series.mean(skipna=True)) * 0.1 or 1.0

        magnitude = None
        if fault_type == "spike":
            magnitude = float(rng.uniform(*SPIKE_MAGNITUDE_STD)) * col_std * rng.choice([-1, 1])
            out[column] = _apply_spike(out[column], mask, magnitude)
        elif fault_type == "drift":
            magnitude = float(rng.uniform(*DRIFT_MAGNITUDE_STD)) * col_std * rng.choice([-1, 1])
            out[column] = _apply_drift(out[column], mask, magnitude)
        elif fault_type == "stuck_at":
            out[column] = _apply_stuck_at(out[column], mask)
        elif fault_type == "dropout":
            out[column] = _apply_dropout(out[column], mask)

        fault_log.append({
            "fault_id": f"{station_id}_{len(fault_log)}",
            "station_id": station_id,
            "start": start_time,
            "end": end_time,
            "parameter": column,
            "fault_type": fault_type,
            "magnitude": magnitude,
        })

    return out, fault_log

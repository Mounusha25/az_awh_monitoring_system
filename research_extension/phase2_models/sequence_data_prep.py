"""
AWH Phase 2 — raw per-timestep sequence extraction for the LSTM attribution
model. Every other model in this package (rule baseline, Isolation Forest,
two-stage, supervised classifier) consumes only the windowed summary stats
in train/val/test.parquet (rel_mean, rel_std, ..., see data_prep.py). An LSTM
is only worth trying if it can see the raw temporal shape within a window
(a ramp for drift, a flatline for stuck_at, a sudden gap for dropout) that
snapshot stats compress away — so this module reconstructs that per-timestep
view from the same deterministic fault injection used to build the
benchmark (see build_benchmark_dataset.py::build_windows(return_faulted=True)).

Each window becomes a fixed-length (SEQ_LEN, 2 * n_features) array: for every
feature, one channel of baseline-relative normalized value (same rolling
per-station/per-feature baseline as compute_windows, for the same reason —
non-stationary features like energy don't generalize across a time split in
absolute terms) and one mask channel (1 = reading present, 0 = missing/
dropout), so the model can distinguish "value is exactly at baseline" from
"value is missing" instead of conflating them behind a single 0 fill.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from build_benchmark_dataset import FEATURE_COLUMNS, rolling_baseline

SEQ_LEN = 30  # matches the ~1 reading/minute, 30-minute window (median record_count == 30)
N_FEATURES = len(FEATURE_COLUMNS)
N_CHANNELS = 2 * N_FEATURES

# Spike magnitude is 4-8x a column's own std; normalized values can reach
# double digits. Clip rather than let a handful of extreme faulty windows
# dominate the loss / cause exploding gradients — direction and relative
# magnitude below the clip are all real signal, and the mask channel already
# carries "value was here at all" independently of scale.
CLIP_ABS = 10.0


def _resample_indices(n_available: int, seq_len: int) -> np.ndarray:
    """Nearest-neighbor up/down-sample n_available raw rows to exactly
    seq_len positions, via index repetition (no interpolation) — simple and
    adequate given windows are usually already close to seq_len rows."""
    if n_available <= 0:
        return np.zeros(seq_len, dtype=int)
    return np.minimum((np.arange(seq_len) * n_available) // seq_len, n_available - 1)


def extract_window_sequence(
    chunk: pd.DataFrame,
    baselines: dict[str, tuple[float, float]],
    seq_len: int = SEQ_LEN,
) -> np.ndarray:
    """chunk: raw (faulted) rows for one station within [window_start, window_end).
    baselines: {feature: (baseline_mean, baseline_std)} as of this window's start
    (same rolling_baseline used by compute_windows — causal, no leakage)."""
    idx = _resample_indices(len(chunk), seq_len)
    out = np.zeros((seq_len, N_CHANNELS), dtype=np.float32)

    for i, feature in enumerate(FEATURE_COLUMNS):
        raw = chunk[feature].to_numpy()[idx] if len(chunk) else np.full(seq_len, np.nan)
        mean, std = baselines[feature]
        safe_std = std if std and std > 0 else np.nan

        present = ~np.isnan(raw)
        normalized = (raw - mean) / safe_std
        normalized = np.clip(normalized, -CLIP_ABS, CLIP_ABS)
        normalized = np.where(present & ~np.isnan(normalized), normalized, 0.0)

        out[:, 2 * i] = normalized
        out[:, 2 * i + 1] = present.astype(np.float32)

    return out


def build_clean_series_for_station(
    faulted_df: pd.DataFrame, fault_log_for_station: list[dict],
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Per-feature (clean_times, clean_vals) for one station, computed once
    and reused across every window row — this doesn't depend on window_start,
    only on the station's full history and its own fault periods (mirrors
    compute_windows's clean_series dict, built once per station there too)."""
    from build_benchmark_dataset import build_clean_series  # local import: avoids a cycle at module load

    times = faulted_df["time"].to_numpy()
    return {
        feature: build_clean_series(times, faulted_df[feature].to_numpy(), feature, fault_log_for_station)
        for feature in FEATURE_COLUMNS
    }


def baseline_at(
    clean_series: dict[str, tuple[np.ndarray, np.ndarray]], window_start: pd.Timestamp,
) -> dict[str, tuple[float, float]]:
    """Per-feature rolling baseline as of window_start (causal, no leakage) —
    the only per-row-dependent piece; clean_series itself is precomputed once
    per station. `window_start` is passed through as the tz-aware pd.Timestamp
    it already is — clean_times (from build_clean_series) is an object array
    of tz-aware Timestamp scalars, not a real datetime64 array, so converting
    to np.datetime64 here would strip the tz and break comparison."""
    return {
        feature: rolling_baseline(clean_times, clean_vals, window_start)
        for feature, (clean_times, clean_vals) in clean_series.items()
    }

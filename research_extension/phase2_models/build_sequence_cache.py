"""
AWH Phase 2 — build the raw-sequence cache the LSTM attribution model reads.

Loads the already-built train/val/test.parquet (the authoritative label
source every model in this package trains/evaluates against) and, for each
labeled window, re-derives its raw per-timestep sequence from the same
deterministic fault injection that produced those labels
(build_benchmark_dataset.py::build_windows(..., return_faulted=True) — same
seed/stations/target fraction reproduces bit-identical faulted_df/fault_log,
since fault placement is a pure function of the rng and station iteration
order, not of anything computed downstream).

Usage:
  python build_sequence_cache.py
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from build_benchmark_dataset import DATA_DIR, INCLUDED_STATIONS, build_windows
from sequence_data_prep import (
    N_CHANNELS,
    SEQ_LEN,
    baseline_at,
    build_clean_series_for_station,
    extract_window_sequence,
)

SEED = 42
TARGET_ANOMALY_FRAC = 0.175  # must match whatever build_benchmark_dataset.py used to write the current parquets
CACHE_PATH = os.path.join(DATA_DIR, "sequence_cache.npz")


def main():
    train_df = pd.read_parquet(os.path.join(DATA_DIR, "train.parquet"))
    val_df = pd.read_parquet(os.path.join(DATA_DIR, "val.parquet"))
    test_df = pd.read_parquet(os.path.join(DATA_DIR, "test.parquet"))
    labels = pd.concat([train_df, val_df, test_df], ignore_index=True)
    print(f"[SeqCache] {len(labels):,} labeled windows across "
          f"{labels['station_id'].nunique()} stations to build sequences for")

    _, all_faults, all_dead_periods, faulted_data = build_windows(
        SEED, TARGET_ANOMALY_FRAC, INCLUDED_STATIONS, return_faulted=True,
    )
    faults_by_station: dict[int, list[dict]] = {}
    for f in all_faults:
        faults_by_station.setdefault(f["station_id"], []).append(f)
    # Round 10: exclude real dead-sensor periods from the baseline too, same as
    # build_benchmark_dataset.py::compute_windows does for the summary-stat
    # models — otherwise the LSTM's per-timestep baseline would be corrupted by
    # a frozen sensor's zero variance exactly where the tabular models' isn't.
    dead_periods_by_station: dict[int, list[dict]] = {}
    for d in all_dead_periods:
        dead_periods_by_station.setdefault(d["station_id"], []).append(d)

    n = len(labels)
    X = np.zeros((n, SEQ_LEN, N_CHANNELS), dtype=np.float32)
    station_id_arr = labels["station_id"].to_numpy()
    window_start_ns = labels["window_start"].to_numpy().astype("datetime64[ns]").astype(np.int64)

    for station_id, station_rows in labels.groupby("station_id"):
        faulted_df = faulted_data[station_id]
        exclusions = faults_by_station.get(station_id, []) + dead_periods_by_station.get(station_id, [])
        clean_series = build_clean_series_for_station(faulted_df, exclusions)
        times = faulted_df["time"].to_numpy()

        for idx, row in station_rows.iterrows():
            # `times` is an object array of tz-aware Timestamp scalars (see
            # build_clean_series's docstring) — compare against the tz-aware
            # Timestamp directly, not a tz-naive np.datetime64 conversion.
            lo = int(np.searchsorted(times, row["window_start"], side="left"))
            hi = int(np.searchsorted(times, row["window_end"], side="left"))
            chunk = faulted_df.iloc[lo:hi]
            baselines = baseline_at(clean_series, row["window_start"])
            X[idx] = extract_window_sequence(chunk, baselines)

        print(f"[SeqCache] Station {station_id}: {len(station_rows):,} sequences built")

    os.makedirs(DATA_DIR, exist_ok=True)
    np.savez_compressed(
        CACHE_PATH, X=X, station_id=station_id_arr, window_start_ns=window_start_ns,
    )
    print(f"[SeqCache] Wrote {CACHE_PATH} — X shape {X.shape}")


if __name__ == "__main__":
    main()

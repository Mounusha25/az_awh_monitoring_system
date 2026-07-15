"""
AWH Benchmark Dataset Builder — Phase 2: Anomaly Attribution Benchmark

Builds the labeled train/val/test dataset that Phase 2 model training
(LSTM + Isolation Forest, RQ1) trains and evaluates against.

Real station data has no anomaly or attribution labels, so this script
injects synthetic faults (see inject_synthetic_faults.py) into real raw
readings at known times/parameters, then aggregates over the same 30-min/
5-min sliding windows Phase 1's Spark consumer uses
(research_extension/phase1_streaming/consumer.py::build_agg_exprs) — in
pandas, so this script has no Kafka/Spark dependency and can run standalone
against PostgreSQL directly.

Note: unlike consumer.py's windowed_features table (absolute mean/std/min/
max), this benchmark expresses every stat relative to a trailing per-
station/per-feature baseline (see compute_windows) — absolute values don't
generalize across a time-based split for non-stationary features like
cumulative energy, and aren't comparable across features for per-feature
anomaly scoring. If Phase 1's production schema is ever used to feed a
trained Phase 2 model directly, it will need the same relative transform
applied downstream, not just consumed as-is.

Because only the targeted column is perturbed per injected fault, that
column is the unambiguous ground-truth causal parameter for every window
it overlaps.

Usage:
  python build_benchmark_dataset.py
  python build_benchmark_dataset.py --seed 7 --target-anomaly-frac 0.18
"""

from __future__ import annotations

import argparse
import os

import mlflow
import numpy as np
import pandas as pd
import psycopg2

from inject_synthetic_faults import inject_faults

# ---------------------------------------------------------------------------
# Configuration — mirrors phase1_streaming/consumer.py and producer.py
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mounusha@localhost:5432/awh_db")

FEATURE_COLUMNS = [
    "temperature", "humidity", "velocity",
    "outtake_temperature", "outtake_humidity", "outtake_velocity",
    "weight", "voltage", "power", "energy",
]

WINDOW_MINUTES = 30
SLIDE_MINUTES = 5
WINDOW = pd.Timedelta(minutes=WINDOW_MINUTES)
SLIDE = pd.Timedelta(minutes=SLIDE_MINUTES)

# Candidate window starts that could contain a given timestamp t: t can fall
# in any window starting at t_floor, t_floor-5min, ..., t_floor-25min.
CANDIDATE_OFFSETS = [pd.Timedelta(minutes=5 * k) for k in range(WINDOW_MINUTES // SLIDE_MINUTES)]

# Rough average number of windows a single injected fault qualifies as
# anomalous (i.e. clears MIN_OVERLAP_FRACTION below), used only to size how
# many faults to inject per station to hit the target anomaly fraction.
# Empirically ~4 under the overlap-fraction labeling rule (was ~19 when any
# nonzero overlap counted) — recalibrate this if MIN_OVERLAP_FRACTION changes.
AVG_WINDOWS_PER_FAULT = 4

# Stations 3 and 6 hold ~99% of real sensor history (the other 6 are lightly-used
# test/dev units with 6-44 windows each — see CLAUDE.md / PENDING_TASKS.md). All 8
# were attempted; the rest are excluded here rather than diluting the "normal"
# distribution with near-empty stations. Override with --stations for a different scope.
INCLUDED_STATIONS = [3, 6]

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

TRACKING_URI = os.getenv(
    "MLFLOW_TRACKING_URI",
    "sqlite:///" + os.path.join(os.path.dirname(__file__), "..", "phase1_streaming", "mlruns.db"),
)


# ---------------------------------------------------------------------------
# Load raw data
# ---------------------------------------------------------------------------

def load_station_data(stations: list[int]) -> dict[int, pd.DataFrame]:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        query = f"""
            SELECT time, station_id, {', '.join(FEATURE_COLUMNS)}
            FROM measurements
            WHERE station_id = ANY(%s)
            ORDER BY station_id, time
        """
        df = pd.read_sql(query, conn, params=(stations,))
    finally:
        conn.close()

    df["time"] = pd.to_datetime(df["time"], utc=True)
    return {sid: g.reset_index(drop=True) for sid, g in df.groupby("station_id")}


# ---------------------------------------------------------------------------
# Windowing (pandas equivalent of Spark's sliding-window aggregation)
# ---------------------------------------------------------------------------

def candidate_window_starts(times: pd.Series) -> np.ndarray:
    floored = times.dt.floor(f"{SLIDE_MINUTES}min")
    starts = pd.concat([floored - off for off in CANDIDATE_OFFSETS])
    return np.sort(starts.unique())


MIN_RECORDS_PER_WINDOW = 3  # windows with fewer readings make std=0 ambiguous (real vs. just-one-sample)

# Window stats are expressed relative to a trailing per-station/per-feature
# "clean" baseline (raw readings with that feature's own fault periods
# excluded) instead of absolute values. Two features non-stationary over the
# ~11-month deployment (energy accumulates, weight varies by experiment) blew
# up every downstream model: per-feature Isolation Forest scores weren't
# comparable across features, and a supervised classifier trained on
# mid-2025 absolute values had no basis to recognize a mid-2026 fault of the
# same relative shape. Expressing everything as "deviation from recent local
# behavior" fixes both at once.
BASELINE_HOURS = 24
MIN_BASELINE_READINGS = 10


def build_clean_series(times: np.ndarray, values: np.ndarray, feature: str,
                        fault_log_for_station: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Raw (time, value) pairs for one feature with that feature's own fault
    periods excluded, so the rolling baseline never gets contaminated by the
    thing it's supposed to be the "normal" reference for."""
    # times is an object array of tz-aware Timestamp scalars (pandas' .to_numpy()
    # doesn't convert tz-aware datetimes to a real datetime64 dtype) — fault
    # start/end are tz-aware Timestamps too, so compare them as-is.
    mask = np.ones(len(times), dtype=bool)
    for f in fault_log_for_station:
        if f["parameter"] != feature:
            continue
        mask &= ~((times >= f["start"]) & (times < f["end"]))
    return times[mask], values[mask]


def rolling_baseline(clean_times: np.ndarray, clean_vals: np.ndarray, window_start: np.datetime64) -> tuple[float, float]:
    """Causal trailing mean/std as of window_start — only ever looks backward,
    so there's no leakage from the window (or its fault) into its own baseline."""
    lookback_start = window_start - np.timedelta64(BASELINE_HOURS, "h")
    lo = int(np.searchsorted(clean_times, lookback_start, side="left"))
    hi = int(np.searchsorted(clean_times, window_start, side="left"))

    if hi - lo < MIN_BASELINE_READINGS:
        lo = 0  # not enough in the trailing window — expand to full history so far

    vals = clean_vals[lo:hi]
    vals = vals[~np.isnan(vals)]
    if len(vals) < 2:
        return np.nan, np.nan
    return float(vals.mean()), float(vals.std())


def max_run_fraction(vals: pd.Series) -> float:
    """Longest run of consecutive identical readings, as a fraction of the
    window's record count. stuck_at freezes to an exact value (see
    inject_synthetic_faults.py::_apply_stuck_at), so exact equality is the
    right check here — this is a synthetic benchmark, not raw hardware noise."""
    arr = vals.to_numpy()
    n = len(arr)
    if n == 0:
        return 0.0
    changed = np.ones(n, dtype=bool)
    changed[1:] = arr[1:] != arr[:-1]  # NaN != NaN is True, so missing readings break runs
    run_id = np.cumsum(changed)
    run_lengths = np.bincount(run_id)
    return float(run_lengths.max()) / n


def half_split_slope(vals: pd.Series) -> float:
    """Second-half mean minus first-half mean — drift's defining property is a
    gradual ramp, not a shifted average, so a snapshot stat like rel_mean can
    look unremarkable on a half-drifted window even though the shape is
    obviously not flat. This targets drift the same way max_run_fraction
    targets stuck_at."""
    n = len(vals)
    if n < 4:
        return np.nan
    mid = n // 2
    first_half = vals.iloc[:mid]
    second_half = vals.iloc[mid:]
    if first_half.isna().all() or second_half.isna().all():
        return np.nan
    return float(second_half.mean(skipna=True) - first_half.mean(skipna=True))


def compute_windows(df: pd.DataFrame, station_id: int, starts: np.ndarray,
                     fault_log_for_station: list[dict]) -> pd.DataFrame:
    times = df["time"].to_numpy()

    clean_series = {
        feature: build_clean_series(times, df[feature].to_numpy(), feature, fault_log_for_station)
        for feature in FEATURE_COLUMNS
    }

    rows = []
    for start in starts:
        end = start + WINDOW
        lo = int(np.searchsorted(times, start, side="left"))
        hi = int(np.searchsorted(times, end, side="left"))
        if hi - lo < MIN_RECORDS_PER_WINDOW:
            continue
        chunk = df.iloc[lo:hi]
        row = {
            "station_id": station_id,
            "window_start": pd.Timestamp(start),
            "window_end": pd.Timestamp(end),
            "record_count": hi - lo,
        }
        for col in FEATURE_COLUMNS:
            vals = chunk[col]
            w_mean = vals.mean(skipna=True)
            w_std = vals.std(skipna=True)
            w_min = vals.min(skipna=True)
            w_max = vals.max(skipna=True)

            clean_times, clean_vals = clean_series[col]
            baseline_mean, baseline_std = rolling_baseline(clean_times, clean_vals, start)
            safe_std = baseline_std if baseline_std and baseline_std > 0 else np.nan

            row[f"{col}_rel_mean"] = (w_mean - baseline_mean) / safe_std
            row[f"{col}_rel_std"] = w_std / safe_std
            row[f"{col}_rel_min"] = (w_min - baseline_mean) / safe_std
            row[f"{col}_rel_max"] = (w_max - baseline_mean) / safe_std
            row[f"{col}_rel_slope"] = half_split_slope(vals) / safe_std
            # dropout faults null out raw values — surface that directly instead of
            # letting mean/min/max imputation silently erase the missing-data signal
            row[f"{col}_missing_frac"] = vals.isna().mean()
            # stuck_at freezes a sensor at a constant value — a frozen run barely
            # moves mean/std/min/max if real readings nearby are already calm, so
            # surface "how much of this window is one repeated value" directly
            row[f"{col}_max_run_frac"] = max_run_fraction(vals)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Labeling — attach is_anomaly / anomaly_type / causal_parameter
# ---------------------------------------------------------------------------

MIN_OVERLAP_FRACTION = 0.5  # of min(fault_duration, window_duration) — a
# sliding window that only grazes the edge of a fault is genuinely
# almost-normal data; labeling it anomalous is label noise no model could
# recover from. Normalizing by the SHORTER of the two matters: for a short
# fault (spike, ~10min) a window must capture most of the fault; for a long
# fault (drift, up to 240min) a single 30-min window can never capture more
# than 30 minutes of it, so normalizing by fault duration alone would make
# long faults mathematically unlabelable (30/240=0.2, never reaches 0.5) —
# normalizing by the window instead means "most of this window falls inside
# the fault," which is achievable and correct for faults longer than a window.


def label_windows(windows: pd.DataFrame, fault_log: list[dict]) -> pd.DataFrame:
    out = windows.copy()
    out["is_anomaly"] = False
    out["anomaly_type"] = "none"
    out["causal_parameter"] = "none"
    out["fault_id"] = "none"  # links a labeled window back to the fault instance that
    # caused it — thousands of windows come from only ~150-250 independent fault
    # events, so evaluation needs to resample by fault instance, not by window
    # (see evaluate.py::bootstrap_ci), or F1 comparisons overstate their own precision.

    if not fault_log:
        return out

    faults_by_station: dict[int, list[dict]] = {}
    for f in fault_log:
        faults_by_station.setdefault(f["station_id"], []).append(f)

    for idx, row in out.iterrows():
        candidates = faults_by_station.get(row["station_id"])
        if not candidates:
            continue
        best = None
        best_overlap_frac = 0.0
        for f in candidates:
            overlap_start = max(f["start"], row["window_start"])
            overlap_end = min(f["end"], row["window_end"])
            overlap = overlap_end - overlap_start
            if overlap <= pd.Timedelta(0):
                continue
            fault_duration = f["end"] - f["start"]
            normalizer = min(fault_duration, WINDOW)
            overlap_frac = overlap / normalizer
            if overlap_frac > best_overlap_frac:
                best_overlap_frac = overlap_frac
                best = f
        if best is not None and best_overlap_frac >= MIN_OVERLAP_FRACTION:
            out.at[idx, "is_anomaly"] = True
            out.at[idx, "anomaly_type"] = best["fault_type"]
            out.at[idx, "causal_parameter"] = best["parameter"]
            out.at[idx, "fault_id"] = best["fault_id"]

    return out


# ---------------------------------------------------------------------------
# Time-based split with embargo gaps to prevent window leakage across splits
# ---------------------------------------------------------------------------

def time_based_split(windows: pd.DataFrame) -> dict[str, pd.DataFrame]:
    windows = windows.sort_values("window_start").reset_index(drop=True)
    t1 = windows["window_start"].quantile(0.70)
    t2 = windows["window_start"].quantile(0.85)
    embargo = WINDOW  # one window's worth of gap so no window spans a boundary

    train = windows[windows["window_end"] <= t1]
    val = windows[(windows["window_start"] >= t1 + embargo) & (windows["window_end"] <= t2)]
    test = windows[windows["window_start"] >= t2 + embargo]

    return {"train": train, "val": val, "test": test}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_dataset(seed: int, target_anomaly_frac: float,
                   stations: list[int] = INCLUDED_STATIONS) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    rng = np.random.default_rng(seed)

    print(f"[Benchmark] Loading raw station data from PostgreSQL (stations={stations})...")
    station_data = load_station_data(stations)
    print(f"[Benchmark] Loaded {sum(len(df) for df in station_data.values()):,} rows "
          f"across {len(station_data)} stations")

    all_windows = []
    all_faults: list[dict] = []

    for station_id, df in station_data.items():
        starts = candidate_window_starts(df["time"])
        if len(starts) == 0:
            continue

        # Estimate total windows to size fault count for the target anomaly fraction
        n_windows_estimate = len(starts)
        n_faults = max(3, round(target_anomaly_frac * n_windows_estimate / AVG_WINDOWS_PER_FAULT))

        faulted_df, fault_log = inject_faults(
            df, station_id, FEATURE_COLUMNS, rng, n_faults=n_faults,
        )
        all_faults.extend(fault_log)

        windows = compute_windows(faulted_df, station_id, starts, fault_log)
        windows = label_windows(windows, fault_log)
        all_windows.append(windows)

        print(f"[Benchmark] Station {station_id}: {len(windows):,} windows, "
              f"{n_faults} faults injected, "
              f"{windows['is_anomaly'].mean() * 100:.1f}% anomalous")

    combined = pd.concat(all_windows, ignore_index=True)
    splits = time_based_split(combined)
    return splits, all_faults


def log_to_mlflow(splits: dict[str, pd.DataFrame], all_faults: list[dict], seed: int, target_frac: float):
    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment("AWH-AnomalyDetection")

    with mlflow.start_run(run_name="phase2-benchmark-dataset"):
        mlflow.log_params({
            "seed": seed,
            "target_anomaly_frac": target_frac,
            "window_minutes": WINDOW_MINUTES,
            "slide_minutes": SLIDE_MINUTES,
            "n_features": len(FEATURE_COLUMNS),
            "fault_types": ",".join(["spike", "drift", "stuck_at", "dropout"]),
        })
        for split_name, df in splits.items():
            mlflow.log_metrics({
                f"{split_name}_rows": len(df),
                f"{split_name}_anomaly_frac": float(df["is_anomaly"].mean()) if len(df) else 0.0,
            })
        mlflow.log_metric("total_faults_injected", len(all_faults))
        mlflow.set_tags({"phase": "2", "stage": "dataset-prep", "rq": "RQ1"})

    print("[Benchmark] Logged dataset-prep run to MLflow experiment AWH-AnomalyDetection")


def main():
    parser = argparse.ArgumentParser(description="Build the AWH anomaly-attribution benchmark dataset")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--target-anomaly-frac", type=float, default=0.175,
                         help="Approximate target fraction of windows that should be anomalous")
    parser.add_argument("--stations", type=str, default=None,
                         help="Comma-separated station IDs (default: INCLUDED_STATIONS = 3,6)")
    args = parser.parse_args()

    stations = [int(s) for s in args.stations.split(",")] if args.stations else INCLUDED_STATIONS
    splits, all_faults = build_dataset(args.seed, args.target_anomaly_frac, stations)

    os.makedirs(DATA_DIR, exist_ok=True)
    for split_name, df in splits.items():
        path = os.path.join(DATA_DIR, f"{split_name}.parquet")
        df.to_parquet(path, index=False)
        print(f"[Benchmark] Wrote {len(df):,} rows to {path}")

    fault_log_path = os.path.join(DATA_DIR, "fault_log.csv")
    pd.DataFrame(all_faults).to_csv(fault_log_path, index=False)
    print(f"[Benchmark] Wrote {len(all_faults):,} fault records to {fault_log_path}")

    log_to_mlflow(splits, all_faults, args.seed, args.target_anomaly_frac)

    print("\n[Benchmark] Summary:")
    for split_name, df in splits.items():
        if len(df) == 0:
            print(f"  {split_name}: 0 rows")
            continue
        print(f"  {split_name}: {len(df):,} rows, "
              f"{df['window_start'].min()} to {df['window_start'].max()}, "
              f"{df['is_anomaly'].mean() * 100:.1f}% anomalous")


if __name__ == "__main__":
    main()

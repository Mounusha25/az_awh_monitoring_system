"""
AWH Benchmark Dataset Builder — Phase 2: Anomaly Attribution Benchmark

Builds the labeled train/val/test dataset that Phase 2 model training
(LSTM + Isolation Forest, RQ1) trains and evaluates against.

Real station data has no anomaly or attribution labels, so this script
injects synthetic faults (see inject_synthetic_faults.py) into real raw
readings at known times/parameters, then recomputes the exact same
30-min/5-min sliding-window statistics Phase 1's Spark consumer computes
(research_extension/phase1_streaming/consumer.py::build_agg_exprs) — in
pandas, so this script has no Kafka/Spark dependency and can run standalone
against PostgreSQL directly.

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

# Rough average number of windows a single injected fault touches
# (duration / slide + windows-of-overlap-at-each-end), used only to size
# how many faults to inject per station to hit the target anomaly fraction.
AVG_WINDOWS_PER_FAULT = 19

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

TRACKING_URI = os.getenv(
    "MLFLOW_TRACKING_URI",
    "sqlite:///" + os.path.join(os.path.dirname(__file__), "..", "phase1_streaming", "mlruns.db"),
)


# ---------------------------------------------------------------------------
# Load raw data
# ---------------------------------------------------------------------------

def load_station_data() -> dict[int, pd.DataFrame]:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        query = f"""
            SELECT time, station_id, {', '.join(FEATURE_COLUMNS)}
            FROM measurements
            ORDER BY station_id, time
        """
        df = pd.read_sql(query, conn)
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


def compute_windows(df: pd.DataFrame, station_id: int, starts: np.ndarray) -> pd.DataFrame:
    times = df["time"].to_numpy()
    rows = []
    for start in starts:
        end = start + WINDOW
        lo = int(np.searchsorted(times, start, side="left"))
        hi = int(np.searchsorted(times, end, side="left"))
        if hi - lo == 0:
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
            row[f"{col}_mean"] = vals.mean(skipna=True)
            row[f"{col}_std"] = vals.std(skipna=True)
            row[f"{col}_min"] = vals.min(skipna=True)
            row[f"{col}_max"] = vals.max(skipna=True)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Labeling — attach is_anomaly / anomaly_type / causal_parameter
# ---------------------------------------------------------------------------

def label_windows(windows: pd.DataFrame, fault_log: list[dict]) -> pd.DataFrame:
    out = windows.copy()
    out["is_anomaly"] = False
    out["anomaly_type"] = "none"
    out["causal_parameter"] = "none"

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
        best_overlap = pd.Timedelta(0)
        for f in candidates:
            overlap_start = max(f["start"], row["window_start"])
            overlap_end = min(f["end"], row["window_end"])
            overlap = overlap_end - overlap_start
            if overlap > best_overlap:
                best_overlap = overlap
                best = f
        if best is not None:
            out.at[idx, "is_anomaly"] = True
            out.at[idx, "anomaly_type"] = best["fault_type"]
            out.at[idx, "causal_parameter"] = best["parameter"]

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

def build_dataset(seed: int, target_anomaly_frac: float) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    rng = np.random.default_rng(seed)

    print("[Benchmark] Loading raw station data from PostgreSQL...")
    station_data = load_station_data()
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

        windows = compute_windows(faulted_df, station_id, starts)
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
    args = parser.parse_args()

    splits, all_faults = build_dataset(args.seed, args.target_anomaly_frac)

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

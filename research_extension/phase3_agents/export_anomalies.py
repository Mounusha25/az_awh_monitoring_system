"""
AWH — export model-flagged anomaly windows for the dashboard overlay.

Scores recent sensor data with the saved Phase 2 Isolation Forest ensemble and
writes a small JSON file of flagged time intervals per station. The dashboard
draws these as shaded bands on its charts.

What this is, and is not
- It is the real saved model run on real recent readings, using the same
  window features as the benchmark (30-min windows, 5-min step, statistics
  relative to a trailing 24h clean baseline).
- It is a batch export, not live inference: the file is as fresh as the last
  time this script ran, and the JSON says so (`generated_at`).
- The model was trained and evaluated on INJECTED synthetic faults (see
  PENDING_TASKS.md), and its attribution F1 is 0.415. A flagged window means
  "unusual compared with this station's recent behavior", not a confirmed
  incident. The dashboard labels it accordingly.

Usage:
  python export_anomalies.py [--days 30] [--out ../../awh_az/water-station-dashboard/public/anomalies.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
import psycopg2

PHASE2_DIR = os.path.join(os.path.dirname(__file__), "..", "phase2_models")
sys.path.insert(0, PHASE2_DIR)

from build_benchmark_dataset import (  # noqa: E402
    DATABASE_URL,
    FEATURE_COLUMNS,
    WINDOW,
    candidate_window_starts,
    compute_windows,
    detect_dead_periods,
    load_station_data,
)
from data_prep import all_stat_columns  # noqa: E402

MODEL_PATH = os.path.join(PHASE2_DIR, "data", "models", "isolation_forest_ensemble.joblib")
# Windows closer together than this are merged into one interval for display.
MERGE_GAP = pd.Timedelta(minutes=10)
# Reliability guardrails. The model's threshold was calibrated on a benchmark
# with ~20% injected faults, and it was never validated on real incidents. If it
# flags more than this share of a station's windows, it is not singling out
# moments, it is describing the station's normal behaviour as anomalous — an
# overlay built on that is noise, so the station is suppressed instead.
MAX_FLAGGED_FRACTION = 0.15
MIN_WINDOWS = 200
DEFAULT_OUT = os.path.join(
    os.path.dirname(__file__), "..", "..", "awh_az", "water-station-dashboard", "public", "anomalies.json"
)


def station_names() -> dict[int, str]:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT station_id, station_name FROM stations")
            return {int(i): n for i, n in cur.fetchall()}
    finally:
        conn.close()


def merge_intervals(flagged: pd.DataFrame) -> list[dict]:
    """Merge overlapping / adjacent flagged windows into display intervals."""
    if flagged.empty:
        return []
    flagged = flagged.sort_values("window_start")
    out: list[dict] = []
    cur = None
    for _, r in flagged.iterrows():
        if cur is not None and r["window_start"] <= cur["end"] + MERGE_GAP:
            cur["end"] = max(cur["end"], r["window_end"])
            cur["windows"] += 1
            cur["scores"].append(float(r["detection_score"]))
            cur["params"].append(str(r["causal_parameter_pred"]))
        else:
            if cur is not None:
                out.append(cur)
            cur = {
                "start": r["window_start"], "end": r["window_end"], "windows": 1,
                "scores": [float(r["detection_score"])], "params": [str(r["causal_parameter_pred"])],
            }
    if cur is not None:
        out.append(cur)

    result = []
    for c in out:
        # The parameter most often blamed across the interval's windows.
        param = max(set(c["params"]), key=c["params"].count)
        result.append({
            "start": c["start"].isoformat(),
            "end": c["end"].isoformat(),
            "parameter": param,
            "score": round(float(np.max(c["scores"])), 3),  # peak detection score
            "windows": c["windows"],
        })
    return result


PARAM_LABEL = {"temperature": "Temperature", "humidity": "Humidity", "weight": "Water weight", "power": "Power"}
BEFORE_WINDOW = pd.Timedelta(hours=3)


def _fmt(param: str, v: float) -> str:
    if param == "temperature":
        return f"{v:.1f} °C"
    if param == "humidity":
        return f"{v:.1f}%"
    if param == "weight":
        return f"{v / 1000:.2f} kg" if abs(v) >= 1000 else f"{v:.0f} g"
    return f"{v:.0f} W"


def describe_interval(df: pd.DataFrame, iv: dict) -> dict:
    """Plain-language description of what actually changed, from raw readings.

    Deliberately not model output: it compares the parameter's readings during
    the interval with the 3 hours before it, so what the dashboard shows is a
    measured fact (before/after values), not the model's opinion of a cause.
    """
    param = iv["parameter"]
    s, e = pd.Timestamp(iv["start"]), pd.Timestamp(iv["end"])
    t = df["time"]
    inside = df.loc[(t >= s) & (t < e), param].dropna()
    before = df.loc[(t >= s - BEFORE_WINDOW) & (t < s), param].dropna()
    if len(inside) < 3 or len(before) < 3:
        return {"summary": f"{PARAM_LABEL[param]} behaved unusually"}

    b, i = float(before.mean()), float(inside.mean())
    scale = max(abs(b), 1e-9)
    if abs(i - b) / scale >= 0.03:
        verb = "rose" if i > b else "fell"
        summary = f"{PARAM_LABEL[param]} {verb} from {_fmt(param, b)} to {_fmt(param, i)}"
    elif float(inside.std()) > 3 * max(float(before.std()), 1e-9):
        summary = f"{PARAM_LABEL[param]} was unusually variable (around {_fmt(param, i)})"
    else:
        summary = f"{PARAM_LABEL[param]} behaved unusually (around {_fmt(param, i)})"
    return {
        "summary": summary,
        "before_mean": round(b, 3),
        "inside_mean": round(i, 3),
        "inside_min": round(float(inside.min()), 3),
        "inside_max": round(float(inside.max()), 3),
    }


def absent_features(df: pd.DataFrame) -> list[str]:
    """Features this station has never reported (entirely NULL for its whole
    history), i.e. the sensor isn't installed. The model was trained only on
    stations that report all four; for one that doesn't, `missing_frac = 1.0`
    reads as a permanent total dropout and flags every window."""
    return [c for c in FEATURE_COLUMNS if df[c].isna().all()]


def predict_present(model, features: pd.DataFrame, absent: list[str]) -> pd.DataFrame:
    """IsolationForestEnsemble.predict, restricted to the sensors a station has.

    The ensemble scores each parameter with its own forest, then takes the max
    across parameters (detection) and the argmax (attribution). Feeding it a
    constant stand-in for an absent sensor doesn't work (it lands in a sparse
    region and scores as anomalous), so absent parameters are left out of the
    max/argmax entirely, using the model's own per-feature scoring methods.
    """
    present = [f for f in FEATURE_COLUMNS if f not in absent]
    det = model.detection_feature_scores(features)[present]
    att = model.attribution_feature_scores(features)[present]
    score = det.max(axis=1)
    flagged = score > model.threshold
    return pd.DataFrame({
        "detection_score": score,
        "is_anomaly_pred": flagged,
        "causal_parameter_pred": att.idxmax(axis=1).where(flagged, "none"),
    }, index=features.index)


def score_station(model, sid: int, df: pd.DataFrame, since: pd.Timestamp) -> tuple[list[dict], int]:
    """Returns (intervals, number of windows scored)."""
    recent_times = df.loc[df["time"] >= since, "time"]
    if recent_times.empty:
        return [], 0
    starts = candidate_window_starts(recent_times)
    # Real frozen-sensor stretches are excluded from the baseline, as in the benchmark.
    windows = compute_windows(df, sid, starts, detect_dead_periods(df, sid))
    if windows.empty:
        return [], 0
    preds = predict_present(model, windows[all_stat_columns()], absent_features(df))
    windows = windows.assign(
        is_anomaly=preds["is_anomaly_pred"].to_numpy().astype(bool),
        causal_parameter_pred=preds["causal_parameter_pred"].to_numpy(),
        detection_score=preds["detection_score"].to_numpy(),
    )
    flagged = windows[windows["is_anomaly"]]
    intervals = merge_intervals(flagged)
    for iv in intervals:
        iv.update(describe_interval(df, iv))
    return intervals, len(windows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--stations", type=int, nargs="*", help="station ids (default: all with data)")
    args = ap.parse_args()

    warnings.filterwarnings("ignore")
    model = joblib.load(MODEL_PATH)
    names = station_names()
    ids = args.stations or sorted(names)
    data = load_station_data(ids)

    result: dict[str, list[dict]] = {}
    suppressed: dict[str, str] = {}
    for sid in ids:
        df = data.get(sid)
        if df is None or df.empty:
            continue
        # A feature that is entirely NULL for a station loads as object dtype; the
        # window code needs floats (NULL -> NaN, which the model already handles).
        df = df.assign(**{c: pd.to_numeric(df[c], errors="coerce") for c in FEATURE_COLUMNS})
        since = df["time"].max() - pd.Timedelta(days=args.days)
        intervals, n = score_station(model, sid, df, since)
        rate = sum(i["windows"] for i in intervals) / n if n else 0
        print(f"station {sid} {names.get(sid)}: {n} windows scored, "
              f"{len(intervals)} intervals, {rate:.1%} of windows flagged")
        if n < MIN_WINDOWS:
            suppressed[names[sid]] = f"too little recent data to score ({n} windows)"
        elif rate > MAX_FLAGGED_FRACTION:
            suppressed[names[sid]] = (
                f"the model flagged {rate:.0%} of this station's recent windows, "
                "which is too many to be informative"
            )
        else:
            result[names[sid]] = intervals

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_minutes": int(WINDOW.total_seconds() // 60),
        "days_scored": args.days,
        "model": "IsolationForestEnsemble (Phase 2)",
        "note": "Unusual compared with each station's recent behavior. Experimental; not a confirmed incident.",
        "max_flagged_fraction": MAX_FLAGGED_FRACTION,
        "stations": result,
        "suppressed": suppressed,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=1)
    print(f"wrote {args.out} ({os.path.getsize(args.out)/1024:.1f} KB)")


if __name__ == "__main__":
    main()

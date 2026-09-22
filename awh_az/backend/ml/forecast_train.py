"""Trains the water-production forecast model and reports honest evaluation
metrics against a naive baseline, before anything gets wired into the API.

Usage:
    python forecast_train.py [station_name] [--source live|file] [--file path.json]

Defaults to station_testbed_1@Powerplant (the only currently-active station)
and pulls its full /hourly history from the live production API. Pass
--source file --file <path> to train from a previously-saved /hourly JSON
response instead (avoids re-hitting the slow full-history endpoint).

Time-based split (not random) for the same reason CLAUDE.md documents for
the rest of this project: a random split on time-series data leaks future
information into training and produces falsely optimistic evaluation.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

from forecast_features import (
    FEATURE_COLUMNS,
    build_training_table,
    hourly_rows_to_frame,
)

API_BASE = "https://az-awh-monitoring-system.onrender.com"
DEFAULT_STATION = "station_testbed_1@Powerplant"
HORIZONS = list(range(1, 49))  # forecast 1-48 hours ahead
TEST_DAYS = 10
VAL_DAYS = 10
MODEL_DIR = Path(__file__).parent / "models"


def fetch_hourly_rows_live(station_name: str) -> list[dict]:
    url = f"{API_BASE}/stations/{urllib.parse.quote(station_name, safe='')}/hourly"
    print(f"Fetching {url} (full history — this can take a minute) ...", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=300) as resp:
        return json.load(resp)["data"]


def fetch_hourly_rows_file(path: str) -> list[dict]:
    return json.load(open(path))["data"]


def time_based_split(as_of: pd.Series, test_days: int, val_days: int):
    max_ts = as_of.max()
    test_cutoff = max_ts - pd.Timedelta(days=test_days)
    val_cutoff = test_cutoff - pd.Timedelta(days=val_days)
    train_mask = as_of < val_cutoff
    val_mask = (as_of >= val_cutoff) & (as_of < test_cutoff)
    test_mask = as_of >= test_cutoff
    return train_mask, val_mask, test_mask


def evaluate(y_true: pd.Series, y_pred: np.ndarray, horizons: pd.Series, label: str):
    mae = mean_absolute_error(y_true, y_pred)
    rmse = mean_squared_error(y_true, y_pred) ** 0.5
    mean_actual = y_true.mean()
    print(f"  {label}: MAE={mae:.4f} L  RMSE={rmse:.4f} L  (mean actual={mean_actual:.4f} L, "
          f"MAE/mean={100*mae/mean_actual:.1f}%)")

    # Break out by horizon bucket — forecast quality should degrade gracefully,
    # not fall off a cliff, as the horizon grows.
    buckets = [(1, 6), (7, 24), (25, 48)]
    for lo, hi in buckets:
        mask = (horizons >= lo) & (horizons <= hi)
        if mask.sum() == 0:
            continue
        bucket_mae = mean_absolute_error(y_true[mask], y_pred[mask])
        print(f"    horizon {lo}-{hi}h (n={mask.sum()}): MAE={bucket_mae:.4f} L")
    return mae, rmse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("station", nargs="?", default=DEFAULT_STATION)
    parser.add_argument("--source", choices=["live", "file"], default="live")
    parser.add_argument("--file", default=None)
    args = parser.parse_args()

    if args.source == "file":
        if not args.file:
            parser.error("--file is required with --source file")
        rows = fetch_hourly_rows_file(args.file)
    else:
        rows = fetch_hourly_rows_live(args.station)

    frame = hourly_rows_to_frame(rows)
    print(f"Loaded {len(frame)} hourly slots, {frame['water_produced_L'].notna().sum()} with real "
          f"water_produced_L, spanning {frame.index.min()} to {frame.index.max()}")

    X, y, as_of = build_training_table(frame, HORIZONS)
    print(f"Built {len(X)} (as-of, horizon) training examples")
    if len(X) < 500:
        print("Too few examples to train a meaningful model — need more history "
              "or a shorter MAX_LOOKBACK_HOURS. Stopping.", file=sys.stderr)
        sys.exit(1)

    train_mask, val_mask, test_mask = time_based_split(as_of, TEST_DAYS, VAL_DAYS)
    print(f"Split: train={train_mask.sum()}  val={val_mask.sum()}  test={test_mask.sum()}")
    if test_mask.sum() < 20 or val_mask.sum() < 20:
        print("Val/test slices too small for this station's history length — "
              "shorten TEST_DAYS/VAL_DAYS or gather more data first.", file=sys.stderr)
        sys.exit(1)

    model = HistGradientBoostingRegressor(
        max_iter=300,
        learning_rate=0.05,
        max_depth=6,
        early_stopping=True,
        random_state=0,
    )
    model.fit(X[train_mask], y[train_mask])

    print("\n=== Model ===")
    evaluate(y[val_mask], model.predict(X[val_mask]), X.loc[val_mask, "horizon_h"], "Validation")
    test_mae, test_rmse = evaluate(
        y[test_mask], model.predict(X[test_mask]), X.loc[test_mask, "horizon_h"], "Test"
    )

    # Naive baseline: "same hour, one week ago" (lag_168h is already a
    # feature) — a model that can't beat this isn't learning anything the
    # raw weekly seasonality didn't already give away for free.
    print("\n=== Baseline: persistence (value 1 week earlier) ===")
    baseline_pred = X["water_produced_L_lag_168h"].fillna(y.mean())
    evaluate(y[val_mask], baseline_pred[val_mask], X.loc[val_mask, "horizon_h"], "Validation")
    base_test_mae, base_test_rmse = evaluate(
        y[test_mask], baseline_pred[test_mask], X.loc[test_mask, "horizon_h"], "Test"
    )

    print(f"\nModel vs. baseline on test: MAE {test_mae:.4f} vs {base_test_mae:.4f} L "
          f"({'better' if test_mae < base_test_mae else 'WORSE — do not ship this'})")

    MODEL_DIR.mkdir(exist_ok=True)
    model_path = MODEL_DIR / "water_forecast_model.joblib"
    joblib.dump(model, model_path)
    metadata = {
        "station_trained_on": args.station,
        "feature_columns": FEATURE_COLUMNS,
        "horizons": HORIZONS,
        "trained_through": str(as_of.max()),
        "n_training_examples": int(train_mask.sum()),
        "test_mae_L": round(float(test_mae), 4),
        "test_rmse_L": round(float(test_rmse), 4),
        "baseline_test_mae_L": round(float(base_test_mae), 4),
    }
    (MODEL_DIR / "water_forecast_model.meta.json").write_text(json.dumps(metadata, indent=2))
    print(f"\nSaved model to {model_path}")
    print(f"Saved metadata to {MODEL_DIR / 'water_forecast_model.meta.json'}")


if __name__ == "__main__":
    main()

"""
Experiment: rolling (trailing-window) percentile-rank calibration for the
Isolation Forest ensemble's attribution scores, vs. the current fixed-
snapshot calibration.

Diagnosis (see PENDING_TASKS.md): percentile-rank scores computed against a
FIXED reference population (train-normal, or a one-shot val-normal
recalibration) go stale under continuous drift over the ~11-month
deployment — a feature's raw score can sit at the 95th+ percentile of a
stale reference even on genuinely normal data, which lets a few "loud"
features (power, energy, and others depending on the period) win the
cross-feature attribution argmax almost regardless of true cause. This
mirrors the exact problem build_benchmark_dataset.py's rolling_baseline()
already solved for raw feature values, one layer up: this script applies the
same trailing-window idea to the SCORE layer instead of a fixed snapshot.

Caveat (documented, not swept under the rug): admission into the trailing
"recent normal" buffer uses ground-truth is_anomaly labels, walking forward
in time per station across train -> val -> test. This is causal in time (no
future information relative to the point being scored) but is NOT a fully
blind online deployment simulation — it uses ground truth for admission
decisions on earlier test windows too, not just train/val. A fully faithful
online simulation would use the model's own prior self-predictions instead;
that's flagged as follow-up work if this experiment shows the idea has
legs.

Usage:
  python experiment_rolling_calibration.py
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right, insort

import numpy as np
import pandas as pd

from build_benchmark_dataset import DATA_DIR
from data_prep import attribution_columns, detection_columns, prepare_columns
from evaluate import detection_f1, attribution_f1, per_fault_type_breakdown
from isolation_forest_model import IsolationForestEnsemble, FEATURE_COLUMNS

import os

LOOKBACK_COUNT = 200  # trailing normal windows kept as the percentile reference
PERCENTILE_THRESHOLD_CANDIDATES = np.concatenate([np.arange(0.5, 0.95, 0.05), np.arange(0.95, 1.0, 0.01)])


def rolling_percentile_rank_causal(raw_scores: np.ndarray, is_normal: np.ndarray, lookback: int) -> np.ndarray:
    """raw_scores/is_normal must already be in time order. For each i, the
    percentile rank of raw_scores[i] among the `lookback` most recent normal
    scores at indices < i. Neutral 0.5 until enough history has accumulated."""
    sorted_ref: list[float] = []   # kept sorted by value, for percentile lookup
    order_buffer: list[float] = []  # kept in time order, for FIFO eviction
    out = np.empty(len(raw_scores))

    for i in range(len(raw_scores)):
        if sorted_ref:
            pos = bisect_right(sorted_ref, raw_scores[i])
            out[i] = pos / len(sorted_ref)
        else:
            out[i] = 0.5

        if is_normal[i]:
            val = float(raw_scores[i])
            insort(sorted_ref, val)
            order_buffer.append(val)
            if len(order_buffer) > lookback:
                oldest = order_buffer.pop(0)
                idx = bisect_left(sorted_ref, oldest)
                del sorted_ref[idx]

    return out


def compute_rolling_scores(model: IsolationForestEnsemble, combined: pd.DataFrame,
                            score_kind: str, lookback: int) -> pd.DataFrame:
    """score_kind: 'detection' or 'attribution'. Returns a DataFrame indexed
    like `combined`, one column per feature, of causal rolling percentile-rank
    scores, computed independently per station."""
    models = model.detection_models_ if score_kind == "detection" else model.attribution_models_
    fill_values = model.detection_fill_values_ if score_kind == "detection" else model.attribution_fill_values_
    columns_fn = detection_columns if score_kind == "detection" else attribution_columns

    out = pd.DataFrame(index=combined.index, columns=FEATURE_COLUMNS, dtype=float)
    for station_id, station_df in combined.groupby("station_id"):
        station_df = station_df.sort_values("window_start")
        is_normal = (~station_df["is_anomaly"]).to_numpy()
        for feature in FEATURE_COLUMNS:
            x, _ = prepare_columns(station_df, columns_fn(feature), fill_values[feature])
            raw = -models[feature].score_samples(x)
            ranks = rolling_percentile_rank_causal(raw, is_normal, lookback)
            out.loc[station_df.index, feature] = ranks
    return out


def predict_with_rolling_scores(detection_scores: pd.DataFrame, attribution_scores: pd.DataFrame,
                                 threshold: float) -> pd.DataFrame:
    detection_score = detection_scores.max(axis=1)
    is_anomaly = detection_score > threshold
    causal_parameter = attribution_scores.idxmax(axis=1)
    causal_parameter = causal_parameter.where(is_anomaly, "none")
    return pd.DataFrame({
        "detection_score": detection_score,
        "is_anomaly_pred": is_anomaly,
        "causal_parameter_pred": causal_parameter,
    }, index=detection_scores.index)


def tune_threshold_on_subset(detection_scores, attribution_scores, df, candidates):
    best_t, best_f1 = candidates[0], -1.0
    for t in candidates:
        preds = predict_with_rolling_scores(detection_scores, attribution_scores, t)
        f1 = detection_f1(df, preds)
        if f1 > best_f1:
            best_f1, best_t = f1, t
    return best_t, best_f1


def main():
    train_df = pd.read_parquet(os.path.join(DATA_DIR, "train.parquet"))
    val_df = pd.read_parquet(os.path.join(DATA_DIR, "val.parquet"))
    test_df = pd.read_parquet(os.path.join(DATA_DIR, "test.parquet"))

    print(f"[Rolling] train={len(train_df):,} val={len(val_df):,} test={len(test_df):,}")
    print(f"[Rolling] val stations: {sorted(val_df['station_id'].unique())} "
          f"(note: only station(s) with rows here get a tuned threshold informed by real val data)")

    model = IsolationForestEnsemble().fit(train_df)

    # train/val/test.parquet are written with index=False, so each split's
    # index restarts at 0 on load — concatenating them directly collides on
    # duplicate indices (0 means three different rows). Tag the split instead
    # of relying on the (already-discarded) original index.
    train_df = train_df.copy(); train_df["_split"] = "train"
    val_df = val_df.copy(); val_df["_split"] = "val"
    test_df = test_df.copy(); test_df["_split"] = "test"

    combined = pd.concat([train_df, val_df, test_df], ignore_index=True)
    combined = combined.sort_values(["station_id", "window_start"]).reset_index(drop=True)

    print(f"[Rolling] Computing rolling detection scores (lookback={LOOKBACK_COUNT} normal windows)...")
    detection_scores = compute_rolling_scores(model, combined, "detection", LOOKBACK_COUNT)
    print("[Rolling] Computing rolling attribution scores...")
    attribution_scores = compute_rolling_scores(model, combined, "attribution", LOOKBACK_COUNT)

    val_idx = combined.index[combined["_split"] == "val"]
    test_idx = combined.index[combined["_split"] == "test"]
    val_slice = combined.loc[val_idx]
    test_slice = combined.loc[test_idx]

    best_thresh, val_f1 = tune_threshold_on_subset(
        detection_scores.loc[val_idx], attribution_scores.loc[val_idx], val_slice, PERCENTILE_THRESHOLD_CANDIDATES
    )
    print(f"[Rolling] Tuned threshold={best_thresh:.2f} (val detection F1={val_f1:.3f})")

    test_preds = predict_with_rolling_scores(
        detection_scores.loc[test_idx], attribution_scores.loc[test_idx], best_thresh
    )
    det_f1 = detection_f1(test_slice, test_preds)
    attr_f1 = attribution_f1(test_slice, test_preds)
    print(f"[Rolling] TEST detection_f1={det_f1:.3f} attribution_f1={attr_f1:.3f}")
    print(per_fault_type_breakdown(test_slice, test_preds).to_string(index=False))

    print("\n[Rolling] Comparison — fixed-snapshot baseline (from prior run): "
          "detection_f1=0.543 attribution_f1=0.331")


if __name__ == "__main__":
    main()

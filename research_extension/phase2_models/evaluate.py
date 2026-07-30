"""
AWH Phase 2 Evaluation — shared metrics for both the rule-based baseline
and the Isolation Forest ensemble, so their numbers are directly comparable
against the proposal's RQ1 targets (F1 > 0.80, baseline F1 < 0.65).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import f1_score

from build_benchmark_dataset import FEATURE_COLUMNS

ATTRIBUTION_LABELS = FEATURE_COLUMNS + ["none"]


def detection_f1(df: pd.DataFrame, predictions: pd.DataFrame) -> float:
    return float(f1_score(df["is_anomaly"], predictions["is_anomaly_pred"]))


def attribution_f1(df: pd.DataFrame, predictions: pd.DataFrame) -> float:
    return float(f1_score(
        df["causal_parameter"],
        predictions["causal_parameter_pred"],
        labels=ATTRIBUTION_LABELS,
        average="macro",
        zero_division=0,
    ))


def tune_threshold(model, val_df: pd.DataFrame, candidates: np.ndarray) -> tuple[float, float]:
    """Sweep `model.threshold`, pick the value maximizing detection F1 on val_df."""
    best_threshold, best_f1 = candidates[0], -1.0
    for candidate in candidates:
        model.threshold = float(candidate)
        preds = model.predict(val_df)
        f1 = detection_f1(val_df, preds)
        if f1 > best_f1:
            best_f1, best_threshold = f1, float(candidate)
    model.threshold = best_threshold
    return best_threshold, best_f1


def per_fault_type_breakdown(df: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    """Detection recall and attribution-given-detection accuracy, split out by
    anomaly_type. A single pooled F1 can hide a fault type collapsing while
    another compensates — this is what the rotating k-fold evaluation checks
    per fold to see whether degradation is fault-type-specific (supports
    small-sample fragility) or uniform (supports drift)."""
    anomalous = df[df["is_anomaly"]].copy()
    anomalous["detected"] = predictions.loc[anomalous.index, "is_anomaly_pred"]
    anomalous["attributed_correctly"] = (
        predictions.loc[anomalous.index, "causal_parameter_pred"] == anomalous["causal_parameter"]
    )

    rows = []
    for fault_type, group in anomalous.groupby("anomaly_type"):
        detected = group["detected"]
        rows.append({
            "anomaly_type": fault_type,
            "n_windows": len(group),
            "n_faults": group["fault_id"].nunique(),
            "detection_recall": float(detected.mean()) if len(group) else float("nan"),
            "attribution_given_detected": float(group.loc[detected, "attributed_correctly"].mean())
                if detected.any() else float("nan"),
        })
    return pd.DataFrame(rows).sort_values("anomaly_type").reset_index(drop=True)


def evaluate_model(model, df: pd.DataFrame) -> dict:
    predictions = model.predict(df)
    return {
        "detection_f1": detection_f1(df, predictions),
        "attribution_f1": attribution_f1(df, predictions),
        "predictions": predictions,
    }


def bootstrap_ci(model, test_df: pd.DataFrame, n_bootstrap: int = 300, seed: int = 0) -> dict:
    """
    Cluster bootstrap over fault INSTANCES, not windows. ~150-250 independent
    injected faults underlie thousands of correlated windows (one fault touches
    many overlapping windows that all carry nearly the same signal) — resampling
    windows directly would treat those as independent evidence when they aren't,
    understating how much a single-point F1 comparison could swing on a
    differently-drawn benchmark. Resampling fault instances (with all of their
    windows moving together) gives a CI that reflects the actual effective
    sample size.
    """
    rng = np.random.default_rng(seed)
    anomalous = test_df[test_df["is_anomaly"]]
    normal = test_df[~test_df["is_anomaly"]]

    fault_ids = anomalous["fault_id"].unique()
    fault_row_groups = {fid: idx.to_numpy() for fid, idx in anomalous.groupby("fault_id").groups.items()}
    normal_idx = normal.index.to_numpy()

    detection_f1s = []
    attribution_f1s = []
    for _ in range(n_bootstrap):
        sampled_fault_ids = rng.choice(fault_ids, size=len(fault_ids), replace=True)
        anom_idx = np.concatenate([fault_row_groups[fid] for fid in sampled_fault_ids])
        sampled_normal_idx = rng.choice(normal_idx, size=len(normal_idx), replace=True)

        resampled = pd.concat([test_df.loc[anom_idx], test_df.loc[sampled_normal_idx]])
        preds = model.predict(resampled)
        detection_f1s.append(detection_f1(resampled, preds))
        attribution_f1s.append(attribution_f1(resampled, preds))

    def summarize(values: list[float]) -> dict:
        arr = np.array(values)
        return {
            "mean": float(arr.mean()),
            "ci_low": float(np.percentile(arr, 2.5)),
            "ci_high": float(np.percentile(arr, 97.5)),
        }

    return {
        "detection_f1": summarize(detection_f1s),
        "attribution_f1": summarize(attribution_f1s),
    }

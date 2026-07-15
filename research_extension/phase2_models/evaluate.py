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


def evaluate_model(model, df: pd.DataFrame) -> dict:
    predictions = model.predict(df)
    return {
        "detection_f1": detection_f1(df, predictions),
        "attribution_f1": attribution_f1(df, predictions),
        "predictions": predictions,
    }

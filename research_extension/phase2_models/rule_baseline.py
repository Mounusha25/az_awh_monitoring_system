"""
AWH Rule-Based Baseline — Phase 2: Anomaly Attribution

Simple per-feature z-score threshold model. This is the comparison point
RQ1 needs to beat (proposal target: rule-based baseline F1 < 0.65). No
attribution-capable rule-based model exists elsewhere in the codebase — the
production "40% false-alert reduction" logic is threshold-based pump/alert
handling, not a causal-attribution model — so this is a fresh, minimal
implementation of the same idea for a fair comparison.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from build_benchmark_dataset import FEATURE_COLUMNS


class RuleBasedBaseline:
    def __init__(self, threshold: float = 3.0):
        self.threshold = threshold
        self.means_: dict[str, float] = {}
        self.stds_: dict[str, float] = {}

    def fit(self, train_df: pd.DataFrame) -> "RuleBasedBaseline":
        normal = train_df[~train_df["is_anomaly"]]
        for feature in FEATURE_COLUMNS:
            col = normal[f"{feature}_mean"]
            self.means_[feature] = float(col.mean())
            self.stds_[feature] = float(col.std()) or 1.0
        return self

    def _zscores(self, df: pd.DataFrame) -> pd.DataFrame:
        scores = {}
        for feature in FEATURE_COLUMNS:
            col = df[f"{feature}_mean"]
            scores[feature] = (col - self.means_[feature]).abs() / self.stds_[feature]
        return pd.DataFrame(scores, index=df.index)

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        zscores = self._zscores(df)
        detection_score = zscores.max(axis=1)
        causal_parameter = zscores.idxmax(axis=1)

        is_anomaly = detection_score > self.threshold
        causal_parameter = causal_parameter.where(is_anomaly, "none")

        return pd.DataFrame({
            "detection_score": detection_score,
            "is_anomaly_pred": is_anomaly,
            "causal_parameter_pred": causal_parameter,
        }, index=df.index)

"""
AWH Two-Stage Model — Phase 2: Anomaly Attribution

Stage 1 (detection): JointIsolationForestDetector decides is_anomaly using
one calibrated score over all 40 columns — no multiple-comparisons
inflation.

Stage 2 (attribution): only for windows Stage 1 flags as anomalous, use
IsolationForestEnsemble's per-feature scores to pick the most-anomalous
feature as the causal parameter. Decoupling the two means attribution
quality is no longer gated by the same threshold that has to control
detection false-positives.

Exposes the same `threshold` / `fit` / `predict` interface as the other
Phase 2 models so it drops into evaluate.py's tune_threshold /
evaluate_model unchanged (threshold controls Stage 1 detection only).
"""

from __future__ import annotations

import pandas as pd

from isolation_forest_model import IsolationForestEnsemble
from joint_detector import JointIsolationForestDetector


class TwoStageModel:
    def __init__(self, threshold: float = 0.95, n_estimators: int = 200, random_state: int = 42):
        self.detector = JointIsolationForestDetector(
            threshold=threshold, n_estimators=n_estimators, random_state=random_state,
        )
        self.attributor = IsolationForestEnsemble(
            n_estimators=n_estimators, random_state=random_state,
        )

    @property
    def threshold(self) -> float:
        return self.detector.threshold

    @threshold.setter
    def threshold(self, value: float):
        self.detector.threshold = value

    @property
    def n_estimators(self) -> int:
        return self.detector.n_estimators

    def fit(self, train_df: pd.DataFrame) -> "TwoStageModel":
        self.detector.fit(train_df)
        self.attributor.fit(train_df)
        return self

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        detection = self.detector.predict(df)
        feature_scores = self.attributor.attribution_feature_scores(df)
        causal_parameter = feature_scores.idxmax(axis=1)
        causal_parameter = causal_parameter.where(detection["is_anomaly_pred"], "none")

        return pd.DataFrame({
            "detection_score": detection["detection_score"],
            "is_anomaly_pred": detection["is_anomaly_pred"],
            "causal_parameter_pred": causal_parameter,
        }, index=df.index)

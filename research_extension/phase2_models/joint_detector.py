"""
AWH Joint Isolation Forest Detector — Phase 2: Anomaly Attribution

A single IsolationForest fit across all 40 windowed stat columns (10
features x mean/std/min/max), trained on windows labeled normal. Produces
one calibrated anomaly score per window.

This exists specifically to avoid the multiple-comparisons inflation that
comes from taking max() across 10 independent per-feature models
(isolation_forest_model.py): on purely normal data, the max of 10 noisy
z-scores is inflated by chance alone, which buries the true signal. A
single joint model over the full 40-dim feature space doesn't have that
problem — it only produces one score, calibrated against its own training
distribution. See two_stage_model.py, which pairs this detector with
IsolationForestEnsemble for attribution.
"""

from __future__ import annotations

import pandas as pd
from sklearn.ensemble import IsolationForest

from data_prep import all_stat_columns, prepare_columns


class JointIsolationForestDetector:
    def __init__(self, threshold: float = 2.0, n_estimators: int = 200, random_state: int = 42):
        self.threshold = threshold
        self.n_estimators = n_estimators
        self.random_state = random_state
        self.columns_ = all_stat_columns()
        self.fill_values_: dict[str, float] = {}
        self.score_mean_ = 0.0
        self.score_std_ = 1.0

    def fit(self, train_df: pd.DataFrame) -> "JointIsolationForestDetector":
        normal = train_df[~train_df["is_anomaly"]]
        x, fill_values = prepare_columns(normal, self.columns_)
        self.fill_values_ = fill_values

        self.model_ = IsolationForest(
            n_estimators=self.n_estimators,
            random_state=self.random_state,
            contamination="auto",
        )
        self.model_.fit(x)

        raw_scores = -self.model_.score_samples(x)
        self.score_mean_ = float(raw_scores.mean())
        self.score_std_ = float(raw_scores.std()) or 1.0
        return self

    def scores(self, df: pd.DataFrame) -> pd.Series:
        x, _ = prepare_columns(df, self.columns_, self.fill_values_)
        raw = -self.model_.score_samples(x)
        z = (raw - self.score_mean_) / self.score_std_
        return pd.Series(z, index=df.index)

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        detection_score = self.scores(df)
        is_anomaly = detection_score > self.threshold
        return pd.DataFrame({
            "detection_score": detection_score,
            "is_anomaly_pred": is_anomaly,
        }, index=df.index)

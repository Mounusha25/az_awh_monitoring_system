"""
AWH Joint Isolation Forest Detector — Phase 2: Anomaly Attribution

A single IsolationForest fit across all stat columns (10 features x
rel_mean/rel_std/rel_min/rel_max/rel_slope/missing_frac/max_run_frac),
trained on windows labeled normal. Produces one calibrated anomaly score
per window, expressed as a percentile rank against its own training-normal
score distribution (see isolation_forest_model.py for why percentile rank
instead of a z-score — it's comparable across differently-shaped/scaled
inputs without assuming Gaussian tails).

This exists specifically to avoid the multiple-comparisons inflation that
comes from taking max() across 10 independent per-feature models
(isolation_forest_model.py): on purely normal data, the max of 10 noisy
scores is inflated by chance alone, which buries the true signal. A
single joint model over the full feature space doesn't have that
problem — it only produces one score, calibrated against its own training
distribution. See two_stage_model.py, which pairs this detector with
IsolationForestEnsemble for attribution.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from data_prep import all_stat_columns, prepare_columns


class JointIsolationForestDetector:
    def __init__(self, threshold: float = 0.95, n_estimators: int = 200, random_state: int = 42):
        self.threshold = threshold  # percentile rank cutoff, in [0, 1]
        self.n_estimators = n_estimators
        self.random_state = random_state
        self.columns_ = all_stat_columns()
        self.fill_values_: dict[str, float] = {}
        self.train_scores_: np.ndarray = np.array([])

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
        self.train_scores_ = np.sort(raw_scores)
        return self

    def scores(self, df: pd.DataFrame) -> pd.Series:
        x, _ = prepare_columns(df, self.columns_, self.fill_values_)
        raw = -self.model_.score_samples(x)
        ranks = np.searchsorted(self.train_scores_, raw, side="right") / len(self.train_scores_)
        return pd.Series(ranks, index=df.index)

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        detection_score = self.scores(df)
        is_anomaly = detection_score > self.threshold
        return pd.DataFrame({
            "detection_score": detection_score,
            "is_anomaly_pred": is_anomaly,
        }, index=df.index)

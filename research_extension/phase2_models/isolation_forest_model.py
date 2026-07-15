"""
AWH Isolation Forest Ensemble — Phase 2: Anomaly Attribution

One IsolationForest per sensor feature, each fit only on that feature's own
stat columns (rel_mean/rel_std/rel_min/rel_max/rel_slope/missing_frac/
max_run_frac) using training windows labeled normal. Because each fault in
the benchmark dataset perturbs exactly one feature, the model whose own
forest scores a window most anomalous is the model's attribution guess for
that window's causal parameter.

Per-feature scores are converted to a percentile rank against that same
model's score distribution on the training normal set, rather than a
z-score. z-scores assume a roughly comparable, roughly-Gaussian-shaped
distribution per feature — a bad assumption once bounded 0-1 features like
missing_frac/max_run_frac sit alongside unbounded rel_mean/rel_std on
completely different scales. Percentile rank ("what fraction of normal
training windows scored lower than this one") is comparable across features
regardless of the underlying score distribution's shape, so a single shared
threshold in [0,1] genuinely means the same thing for every feature.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from build_benchmark_dataset import FEATURE_COLUMNS
from data_prep import prepare_columns, stat_columns


class IsolationForestEnsemble:
    def __init__(self, threshold: float = 0.95, n_estimators: int = 200, random_state: int = 42):
        self.threshold = threshold  # percentile rank cutoff, in [0, 1]
        self.n_estimators = n_estimators
        self.random_state = random_state
        self.models_: dict[str, IsolationForest] = {}
        self.fill_values_: dict[str, dict[str, float]] = {}
        self.train_scores_: dict[str, np.ndarray] = {}

    def fit(self, train_df: pd.DataFrame) -> "IsolationForestEnsemble":
        normal = train_df[~train_df["is_anomaly"]]
        for feature in FEATURE_COLUMNS:
            x, fill_values = prepare_columns(normal, stat_columns(feature))
            self.fill_values_[feature] = fill_values

            model = IsolationForest(
                n_estimators=self.n_estimators,
                random_state=self.random_state,
                contamination="auto",
            )
            model.fit(x)
            self.models_[feature] = model

            # score_samples: higher = more normal. Flip sign so higher = more anomalous.
            raw_scores = -model.score_samples(x)
            self.train_scores_[feature] = np.sort(raw_scores)
        return self

    def _percentile_rank(self, feature: str, raw_scores: np.ndarray) -> np.ndarray:
        train_scores = self.train_scores_[feature]
        ranks = np.searchsorted(train_scores, raw_scores, side="right")
        return ranks / len(train_scores)

    def feature_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        """Per-feature percentile-rank anomaly scores in [0, 1] — higher = more anomalous."""
        scores = {}
        for feature in FEATURE_COLUMNS:
            x, _ = prepare_columns(df, stat_columns(feature), self.fill_values_[feature])
            raw = -self.models_[feature].score_samples(x)
            scores[feature] = self._percentile_rank(feature, raw)
        return pd.DataFrame(scores, index=df.index)

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        scores = self.feature_scores(df)
        detection_score = scores.max(axis=1)
        causal_parameter = scores.idxmax(axis=1)

        is_anomaly = detection_score > self.threshold
        causal_parameter = causal_parameter.where(is_anomaly, "none")

        return pd.DataFrame({
            "detection_score": detection_score,
            "is_anomaly_pred": is_anomaly,
            "causal_parameter_pred": causal_parameter,
        }, index=df.index)

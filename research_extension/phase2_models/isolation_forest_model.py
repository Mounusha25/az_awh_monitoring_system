"""
AWH Isolation Forest Ensemble — Phase 2: Anomaly Attribution

One IsolationForest per sensor feature, each fit only on that feature's own
4 windowed stats (mean/std/min/max) using training windows labeled normal.
Because each fault in the benchmark dataset perturbs exactly one feature,
the model whose own forest scores a window most anomalous is the model's
attribution guess for that window's causal parameter.

Per-feature scores are z-normalized against that same model's score
distribution on the training normal set, so all 10 features land on a
comparable scale before taking the max (detection) / argmax (attribution)
across features.

Note: using max-across-10-features directly for detection suffers from a
multiple-comparisons effect (the max of 10 noisy scores is inflated even on
purely normal data) — see joint_detector.py for the detection model used in
two_stage_model.py. This ensemble's `feature_scores()` is still the
attribution signal: it's only asked "which feature looks worst" for windows
another model has already flagged as anomalous.
"""

from __future__ import annotations

import pandas as pd
from sklearn.ensemble import IsolationForest

from build_benchmark_dataset import FEATURE_COLUMNS
from data_prep import prepare_columns, stat_columns


class IsolationForestEnsemble:
    def __init__(self, threshold: float = 2.0, n_estimators: int = 200, random_state: int = 42):
        self.threshold = threshold
        self.n_estimators = n_estimators
        self.random_state = random_state
        self.models_: dict[str, IsolationForest] = {}
        self.fill_values_: dict[str, dict[str, float]] = {}
        self.score_means_: dict[str, float] = {}
        self.score_stds_: dict[str, float] = {}

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
            self.score_means_[feature] = float(raw_scores.mean())
            self.score_stds_[feature] = float(raw_scores.std()) or 1.0
        return self

    def feature_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        """Per-feature z-normalized anomaly scores — higher = more anomalous."""
        scores = {}
        for feature in FEATURE_COLUMNS:
            x, _ = prepare_columns(df, stat_columns(feature), self.fill_values_[feature])
            raw = -self.models_[feature].score_samples(x)
            z = (raw - self.score_means_[feature]) / self.score_stds_[feature]
            scores[feature] = z
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

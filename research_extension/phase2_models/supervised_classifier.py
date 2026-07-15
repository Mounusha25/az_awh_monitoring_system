"""
AWH Supervised Attribution Classifier — Phase 2: Anomaly Attribution

All three prior models (rule baseline, per-feature Isolation Forest
ensemble, two-stage) attribute causal_parameter by comparing 10
independently-scaled anomaly scores and taking the argmax — which is
inherently noisy: an unrelated feature's ordinary sensor noise can
outscore a real but moderate signal (confirmed empirically: dropout faults
scored a genuinely elevated z-score on the true cause, but still lost the
argmax to an unrelated feature's fluctuation).

This model sidesteps that entirely by using the ground-truth
causal_parameter labels for what they're actually good for: training one
supervised multi-class classifier (11 classes: 10 features + "none")
directly on all windowed stat columns. One model making one joint decision,
not 10 independent models being compared after the fact.
"""

from __future__ import annotations

import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from data_prep import all_stat_columns, prepare_columns


class SupervisedAttributionModel:
    def __init__(self, threshold: float = 0.3, n_estimators: int = 300, random_state: int = 42):
        self.threshold = threshold  # minimum predicted probability to accept a non-"none" label
        self.n_estimators = n_estimators
        self.random_state = random_state
        self.columns_ = all_stat_columns()
        self.fill_values_: dict[str, float] = {}

    def fit(self, train_df: pd.DataFrame) -> "SupervisedAttributionModel":
        x, fill_values = prepare_columns(train_df, self.columns_)
        self.fill_values_ = fill_values

        self.model_ = RandomForestClassifier(
            n_estimators=self.n_estimators,
            random_state=self.random_state,
            class_weight="balanced",
            # Only ~100-140 independent fault instances underlie thousands of
            # overlapping windows — an unconstrained forest memorizes them
            # (97.7% train accuracy, near-total test collapse observed).
            # Shallow trees + a real per-leaf sample floor force it to learn
            # broader patterns instead of per-instance idiosyncrasies.
            max_depth=5,
            min_samples_leaf=20,
        )
        self.model_.fit(x, train_df["causal_parameter"])
        return self

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        x, _ = prepare_columns(df, self.columns_, self.fill_values_)
        proba = self.model_.predict_proba(x)
        classes = self.model_.classes_

        best_idx = proba.argmax(axis=1)
        best_prob = proba.max(axis=1)
        causal_parameter = pd.Series(classes[best_idx], index=df.index)

        low_confidence = best_prob < self.threshold
        causal_parameter = causal_parameter.where(~low_confidence, "none")
        is_anomaly = causal_parameter != "none"

        return pd.DataFrame({
            "detection_score": best_prob,
            "is_anomaly_pred": is_anomaly,
            "causal_parameter_pred": causal_parameter,
        }, index=df.index)

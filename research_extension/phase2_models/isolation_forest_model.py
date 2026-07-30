"""
AWH Isolation Forest Ensemble — Phase 2: Anomaly Attribution

Two IsolationForest models per sensor feature:
  - a DETECTION forest on the 6 "core" stat columns (rel_mean/rel_std/
    rel_min/rel_max/missing_frac/max_run_frac) — decides is_anomaly
  - an ATTRIBUTION forest on those 6 plus rel_slope — only used to rank
    candidate causal parameters among windows already flagged anomalous

This split exists because rel_slope, while genuinely informative for drift
(its defining property is a ramp), is just ordinary sensor noise for fault
types with no ramp shape (dropout, stuck_at) — feeding it into the same
forest used for detection measurably hurt those fault types' recall
(confirmed by ablation). Isolating it to the attribution-only stage keeps
its benefit for drift without polluting detection for the other fault
types — the same "too many dimensions for one forest" problem already
diagnosed for the two-stage model's 70-column joint detector, applied here
at a much smaller scale (6 -> 7 columns) and fixed the same way: decouple
detection from attribution instead of asking one model to do both.

Per-feature scores are converted to a percentile rank against that same
model's score distribution on the training normal set, rather than a
z-score — comparable across features regardless of the underlying score
distribution's shape (bounded 0-1 features like missing_frac/max_run_frac
sit alongside unbounded rel_mean/rel_std on completely different scales).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from build_benchmark_dataset import FEATURE_COLUMNS
from data_prep import attribution_columns, detection_columns, prepare_columns


def _percentile_rank(train_scores: np.ndarray, raw_scores: np.ndarray) -> np.ndarray:
    ranks = np.searchsorted(train_scores, raw_scores, side="right")
    return ranks / len(train_scores)


class IsolationForestEnsemble:
    def __init__(self, threshold: float = 0.95, n_estimators: int = 200, random_state: int = 42):
        self.threshold = threshold  # percentile rank cutoff, in [0, 1] — gates detection only
        self.n_estimators = n_estimators
        self.random_state = random_state

        self.detection_models_: dict[str, IsolationForest] = {}
        self.detection_fill_values_: dict[str, dict[str, float]] = {}
        self.detection_train_scores_: dict[str, np.ndarray] = {}

        self.attribution_models_: dict[str, IsolationForest] = {}
        self.attribution_fill_values_: dict[str, dict[str, float]] = {}
        self.attribution_train_scores_: dict[str, np.ndarray] = {}

    def _fit_one(self, normal: pd.DataFrame, feature: str, columns: list[str]):
        x, fill_values = prepare_columns(normal, columns)
        model = IsolationForest(
            n_estimators=self.n_estimators,
            random_state=self.random_state,
            contamination="auto",
        )
        model.fit(x)
        raw_scores = -model.score_samples(x)  # higher = more anomalous
        return model, fill_values, np.sort(raw_scores)

    def fit(self, train_df: pd.DataFrame) -> "IsolationForestEnsemble":
        normal = train_df[~train_df["is_anomaly"]]
        for feature in FEATURE_COLUMNS:
            model, fill_values, train_scores = self._fit_one(normal, feature, detection_columns(feature))
            self.detection_models_[feature] = model
            self.detection_fill_values_[feature] = fill_values
            self.detection_train_scores_[feature] = train_scores

            model, fill_values, train_scores = self._fit_one(normal, feature, attribution_columns(feature))
            self.attribution_models_[feature] = model
            self.attribution_fill_values_[feature] = fill_values
            self.attribution_train_scores_[feature] = train_scores
        return self

    def recalibrate(self, reference_df: pd.DataFrame) -> "IsolationForestEnsemble":
        """Refresh the percentile-rank reference arrays using a more recent
        "known normal" population, without refitting the IsolationForest
        models themselves.

        Percentile rank is only comparable across features if each feature's
        reference distribution reflects what "normal" looks like *at
        evaluation time*. Calibrating against train-normal windows is
        circular-safe but goes stale over an 11-month deployment: several
        features (power, energy, velocity, voltage, temperature) drift far
        enough that their raw scores sit at the 90-98th percentile of the
        *training* reference even on genuinely normal val/test windows —
        which means they win the cross-feature attribution argmax almost by
        default, regardless of which feature actually caused a given fault
        (confirmed empirically: energy/weight attribution scores during their
        own true faults were high in absolute terms, ~0.97, but still lost
        to power/humidity's near-ceiling score on unrelated windows).

        Pass a chronologically later normal population (e.g. the validation
        split) as `reference_df` — using it only to refresh the score
        percentile-rank lookup (not to refit the forests) keeps this
        leak-free with respect to the test split, since test is never
        touched here.
        """
        normal = reference_df[~reference_df["is_anomaly"]]
        for feature in FEATURE_COLUMNS:
            x, _ = prepare_columns(normal, detection_columns(feature), self.detection_fill_values_[feature])
            raw = -self.detection_models_[feature].score_samples(x)
            self.detection_train_scores_[feature] = np.sort(raw)

            x, _ = prepare_columns(normal, attribution_columns(feature), self.attribution_fill_values_[feature])
            raw = -self.attribution_models_[feature].score_samples(x)
            self.attribution_train_scores_[feature] = np.sort(raw)
        return self

    def detection_feature_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        """Per-feature percentile-rank anomaly scores in [0, 1], 6-column detection forests."""
        scores = {}
        for feature in FEATURE_COLUMNS:
            x, _ = prepare_columns(df, detection_columns(feature), self.detection_fill_values_[feature])
            raw = -self.detection_models_[feature].score_samples(x)
            scores[feature] = _percentile_rank(self.detection_train_scores_[feature], raw)
        return pd.DataFrame(scores, index=df.index)

    def attribution_feature_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        """Per-feature percentile-rank anomaly scores in [0, 1], 7-column attribution forests (adds rel_slope)."""
        scores = {}
        for feature in FEATURE_COLUMNS:
            x, _ = prepare_columns(df, attribution_columns(feature), self.attribution_fill_values_[feature])
            raw = -self.attribution_models_[feature].score_samples(x)
            scores[feature] = _percentile_rank(self.attribution_train_scores_[feature], raw)
        return pd.DataFrame(scores, index=df.index)

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        detection_scores = self.detection_feature_scores(df)
        detection_score = detection_scores.max(axis=1)
        is_anomaly = detection_score > self.threshold

        attribution_scores = self.attribution_feature_scores(df)
        causal_parameter = attribution_scores.idxmax(axis=1)
        causal_parameter = causal_parameter.where(is_anomaly, "none")

        return pd.DataFrame({
            "detection_score": detection_score,
            "is_anomaly_pred": is_anomaly,
            "causal_parameter_pred": causal_parameter,
        }, index=df.index)

"""
Experiment: covariance-adjusted attribution scoring.

Round 6 (PENDING_TASKS.md) found that the argmax-over-independent-per-feature-
scores attribution mechanism is dominated by a common-mode factor: several
features (power, energy, velocity, voltage, temperature) run elevated
TOGETHER on val/test-normal windows relative to their train calibration, so
whichever of them happens to be highest wins the attribution argmax almost
regardless of true cause. Two attempts to fix this by recalibrating the
percentile-rank reference (one-shot against val, rolling/causal against a
trailing normal buffer) both made attribution F1 worse, not better — the
"loud" features may carry real, not purely artifactual, elevated variance in
those periods, and recalibrating erased real signal along with it.

This experiment takes a different angle that doesn't touch calibration at
all: instead of ranking raw per-feature scores, remove the shared common-mode
component first (via PCA on train-normal attribution scores) and rank by
what's LEFT — the feature-specific residual after subtracting out whatever
factor moves all ten features together. A real single-feature fault should
show up as an outlier on ITS residual; a "noisy day" that elevates everything
should mostly load onto the removed common component and wash out of the
residual comparison instead of winning it by default.

Detection is left completely untouched (it already performs reasonably,
~0.54 F1, and isn't part of what Round 6 diagnosed as broken) — only the
attribution step (which feature to blame, among windows already flagged
anomalous) is changed.

Usage:
  python experiment_covariance_attribution.py
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA

from build_benchmark_dataset import DATA_DIR
from evaluate import attribution_f1, detection_f1, per_fault_type_breakdown, tune_threshold
from isolation_forest_model import IsolationForestEnsemble, FEATURE_COLUMNS

PERCENTILE_THRESHOLD_CANDIDATES = np.concatenate([np.arange(0.5, 0.95, 0.05), np.arange(0.95, 1.0, 0.01)])


def covariance_adjusted_scores(pca: PCA, attribution_scores: pd.DataFrame) -> pd.DataFrame:
    """Residual = raw score minus its reconstruction from the top-k PCA
    components fit on train-normal attribution scores. What's left is the
    part of each feature's score that ISN'T explained by the shared
    common-mode factor(s) — i.e. genuinely feature-specific deviation."""
    x = attribution_scores[FEATURE_COLUMNS].to_numpy()
    reconstructed = pca.inverse_transform(pca.transform(x))
    residual = x - reconstructed
    return pd.DataFrame(residual, columns=FEATURE_COLUMNS, index=attribution_scores.index)


def predict_covariance_adjusted(model: IsolationForestEnsemble, pca: PCA, df: pd.DataFrame,
                                 threshold: float) -> pd.DataFrame:
    # Detection stage: completely unchanged from the baseline model.
    detection_scores = model.detection_feature_scores(df)
    detection_score = detection_scores.max(axis=1)
    is_anomaly = detection_score > threshold

    # Attribution stage: covariance-adjusted residual instead of raw score.
    attribution_scores = model.attribution_feature_scores(df)
    residual = covariance_adjusted_scores(pca, attribution_scores)
    causal_parameter = residual.idxmax(axis=1)
    causal_parameter = causal_parameter.where(is_anomaly, "none")

    return pd.DataFrame({
        "detection_score": detection_score,
        "is_anomaly_pred": is_anomaly,
        "causal_parameter_pred": causal_parameter,
    }, index=df.index)


def main():
    train_df = pd.read_parquet(os.path.join(DATA_DIR, "train.parquet"))
    val_df = pd.read_parquet(os.path.join(DATA_DIR, "val.parquet"))
    test_df = pd.read_parquet(os.path.join(DATA_DIR, "test.parquet"))
    print(f"[CovAttr] train={len(train_df):,} val={len(val_df):,} test={len(test_df):,}")

    model = IsolationForestEnsemble().fit(train_df)

    train_normal = train_df[~train_df["is_anomaly"]]
    train_attr_scores = model.attribution_feature_scores(train_normal)

    for n_components in (1, 2, 3):
        pca = PCA(n_components=n_components, random_state=42)
        pca.fit(train_attr_scores[FEATURE_COLUMNS].to_numpy())
        explained = pca.explained_variance_ratio_.sum()

        # Tune threshold on val using the SAME detection stage as baseline
        # (unchanged), just wrapped so we can reuse tune_threshold's interface.
        class _Wrapped:
            def __init__(self, threshold):
                self.threshold = threshold
            def predict(self, df):
                return predict_covariance_adjusted(model, pca, df, self.threshold)

        wrapped = _Wrapped(threshold=0.95)
        best_thresh, val_f1 = tune_threshold(wrapped, val_df, PERCENTILE_THRESHOLD_CANDIDATES)

        test_preds = predict_covariance_adjusted(model, pca, test_df, best_thresh)
        det_f1 = detection_f1(test_df, test_preds)
        attr_f1 = attribution_f1(test_df, test_preds)

        print(f"\n[CovAttr] n_components={n_components} (explains {explained:.1%} of train-normal "
              f"score variance) threshold={best_thresh:.2f} (val F1={val_f1:.3f})")
        print(f"[CovAttr] TEST detection_f1={det_f1:.3f} attribution_f1={attr_f1:.3f}")
        print(per_fault_type_breakdown(test_df, test_preds).to_string(index=False))

    print("\n[CovAttr] Baseline for comparison (unchanged model, prior run): "
          "detection_f1=0.543 attribution_f1=0.331")


if __name__ == "__main__":
    main()

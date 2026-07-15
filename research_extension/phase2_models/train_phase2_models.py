"""
AWH Phase 2 — Train and Evaluate Anomaly Attribution Models

Fits the rule-based baseline and the per-feature Isolation Forest ensemble
on the benchmark dataset, tunes each model's detection threshold on the
validation split, evaluates both on the held-out test split, and reports
results against the proposal's RQ1 targets (attribution F1 > 0.80,
rule-based baseline F1 < 0.65).

LSTM temporal model is not included here — deferred per the sparse-station
data limitation (see plan notes / CLAUDE.md PENDING_TASKS).

Usage:
  python train_phase2_models.py
"""

from __future__ import annotations

import os

import joblib
import mlflow
import numpy as np
import pandas as pd

from build_benchmark_dataset import DATA_DIR, TRACKING_URI
from evaluate import evaluate_model, tune_threshold
from isolation_forest_model import IsolationForestEnsemble
from rule_baseline import RuleBasedBaseline

MODELS_DIR = os.path.join(DATA_DIR, "models")
THRESHOLD_CANDIDATES = np.arange(0.5, 6.25, 0.25)

RQ1_TARGET_F1 = 0.80
RQ1_BASELINE_F1_CEILING = 0.65


def load_splits() -> dict[str, pd.DataFrame]:
    return {
        name: pd.read_parquet(os.path.join(DATA_DIR, f"{name}.parquet"))
        for name in ("train", "val", "test")
    }


def log_run(run_name: str, model_type: str, threshold: float, test_metrics: dict, extra_params: dict):
    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment("AWH-AnomalyDetection")
    with mlflow.start_run(run_name=run_name):
        mlflow.log_params({"model_type": model_type, "threshold": threshold, **extra_params})
        mlflow.log_metrics({
            "test_detection_f1": test_metrics["detection_f1"],
            "test_attribution_f1": test_metrics["attribution_f1"],
        })
        mlflow.set_tags({"phase": "2", "stage": "model-training", "rq": "RQ1"})


def main():
    splits = load_splits()
    train_df, val_df, test_df = splits["train"], splits["val"], splits["test"]
    print(f"[Phase2] train={len(train_df):,} val={len(val_df):,} test={len(test_df):,}")

    os.makedirs(MODELS_DIR, exist_ok=True)
    results = {}

    # --- Rule-based baseline ---
    baseline = RuleBasedBaseline().fit(train_df)
    best_thresh, val_f1 = tune_threshold(baseline, val_df, THRESHOLD_CANDIDATES)
    baseline_metrics = evaluate_model(baseline, test_df)
    print(f"[Phase2] Rule baseline — threshold={best_thresh:.2f} (val F1={val_f1:.3f}) "
          f"test_detection_f1={baseline_metrics['detection_f1']:.3f} "
          f"test_attribution_f1={baseline_metrics['attribution_f1']:.3f}")
    joblib.dump(baseline, os.path.join(MODELS_DIR, "rule_baseline.joblib"))
    log_run("phase2-rule-baseline", "rule_based_zscore", best_thresh, baseline_metrics, {})
    results["rule_baseline"] = baseline_metrics

    # --- Isolation Forest ensemble ---
    iso_forest = IsolationForestEnsemble().fit(train_df)
    best_thresh, val_f1 = tune_threshold(iso_forest, val_df, THRESHOLD_CANDIDATES)
    iso_metrics = evaluate_model(iso_forest, test_df)
    print(f"[Phase2] Isolation Forest ensemble — threshold={best_thresh:.2f} (val F1={val_f1:.3f}) "
          f"test_detection_f1={iso_metrics['detection_f1']:.3f} "
          f"test_attribution_f1={iso_metrics['attribution_f1']:.3f}")
    joblib.dump(iso_forest, os.path.join(MODELS_DIR, "isolation_forest_ensemble.joblib"))
    log_run("phase2-isolation-forest", "isolation_forest_per_feature", best_thresh, iso_metrics,
            {"n_estimators": iso_forest.n_estimators})
    results["isolation_forest"] = iso_metrics

    # --- Comparison against RQ1 targets ---
    print("\n[Phase2] RQ1 comparison:")
    print(f"  Target attribution F1:            > {RQ1_TARGET_F1}")
    print(f"  Rule-based baseline ceiling:       < {RQ1_BASELINE_F1_CEILING}")
    print(f"  Rule-based baseline attribution F1:  {results['rule_baseline']['attribution_f1']:.3f}")
    print(f"  Isolation Forest attribution F1:     {results['isolation_forest']['attribution_f1']:.3f}")

    # --- Spot check: does per-feature scoring pick the right causal parameter? ---
    print("\n[Phase2] Spot check (Isolation Forest attribution on true anomalies):")
    anomalous_test = test_df[test_df["is_anomaly"]].sample(min(5, test_df["is_anomaly"].sum()), random_state=1)
    spot_preds = iso_forest.predict(anomalous_test)
    for idx in anomalous_test.index:
        true_param = test_df.loc[idx, "causal_parameter"]
        pred_param = spot_preds.loc[idx, "causal_parameter_pred"]
        match = "✓" if true_param == pred_param else "✗"
        print(f"  {match} true={true_param:<20} predicted={pred_param}")

    return results


if __name__ == "__main__":
    main()

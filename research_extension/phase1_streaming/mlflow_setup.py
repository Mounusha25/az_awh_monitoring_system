"""
AWH MLflow Setup — Phase 1: Streaming Foundation

Initializes the MLflow tracking server and experiment structure for
all three research phases. Run once before Phase 2 model training.

Creates:
  - Experiment: AWH-AnomalyDetection   (Phase 2 — LSTM + Isolation Forest)
  - Experiment: AWH-DriftAdaptation    (Phase 4 — retraining pipeline)
  - Experiment: AWH-StakeholderEval    (Phase 5 — LLM incident summary evaluation)
  - Registered model placeholders for champion/challenger pattern

Usage:
  # Option A: local filesystem tracking (default — no extra setup)
  python mlflow_setup.py

  # Option B: remote MLflow server (set MLFLOW_TRACKING_URI first)
  export MLFLOW_TRACKING_URI=http://localhost:5001
  mlflow server --backend-store-uri sqlite:///mlflow.db --default-artifact-root ./mlruns &
  python mlflow_setup.py
"""

import os

import mlflow
from mlflow.tracking import MlflowClient

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "sqlite:///mlruns.db")

EXPERIMENTS = [
    {
        "name":        "AWH-AnomalyDetection",
        "tags": {
            "phase":       "2",
            "description": "LSTM temporal model + Isolation Forest ensemble for co-anomaly detection",
            "target_f1":   "0.80",
            "baseline_f1": "0.65",
            "rq":          "RQ1",
        },
    },
    {
        "name":        "AWH-DriftAdaptation",
        "tags": {
            "phase":       "4",
            "description": "Evidently AI drift detection + Airflow-triggered retraining",
            "target":      "recover to within 5% of pre-drift F1 within 48 hours",
            "rq":          "RQ2",
        },
    },
    {
        "name":        "AWH-StakeholderEval",
        "tags": {
            "phase":       "5",
            "description": "LLM-generated incident summary evaluation against numeric alert tables",
            "target":      "25% faster correct intervention decisions",
            "rq":          "RQ3",
        },
    },
]

# Models registered here for champion/challenger tracking (Phase 2+)
MODEL_NAMES = [
    "awh-lstm-anomaly",
    "awh-isolation-forest",
    "awh-ensemble",
]


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_mlflow():
    mlflow.set_tracking_uri(TRACKING_URI)
    client = MlflowClient()

    print(f"[MLflow] Tracking URI: {TRACKING_URI}")

    # Create experiments
    for exp in EXPERIMENTS:
        existing = client.get_experiment_by_name(exp["name"])
        if existing:
            print(f"[MLflow] Experiment already exists: {exp['name']} (ID: {existing.experiment_id})")
        else:
            exp_id = client.create_experiment(exp["name"], tags=exp["tags"])
            print(f"[MLflow] Created experiment: {exp['name']} (ID: {exp_id})")

    # Register model placeholders
    for model_name in MODEL_NAMES:
        try:
            client.create_registered_model(
                name=model_name,
                tags={
                    "project": "AzAWH",
                    "framework": "pytorch" if "lstm" in model_name else "sklearn",
                },
                description=f"AWH anomaly detection model: {model_name}"
            )
            print(f"[MLflow] Registered model created: {model_name}")
        except mlflow.exceptions.MlflowException as e:
            if "already exists" in str(e).lower():
                print(f"[MLflow] Model already registered: {model_name}")
            else:
                raise

    print("\n[MLflow] Setup complete")
    print(f"[MLflow] View experiments: mlflow ui --backend-store-uri {TRACKING_URI}")
    print(f"[MLflow] (runs on http://localhost:5000 by default)")

    # Log a Phase 1 baseline run to confirm tracking works
    mlflow.set_experiment("AWH-AnomalyDetection")
    with mlflow.start_run(run_name="phase1-streaming-baseline"):
        mlflow.log_params({
            "window_duration":  "30 minutes",
            "slide_duration":   "5 minutes",
            "watermark":        "10 minutes",
            "n_features":       10,
            "feature_stats":    "mean,std,min,max",
            "stations":         8,
            "total_records":    40969,  # update after windowed_features is populated
        })
        mlflow.log_metrics({
            "windowed_feature_rows": 0,   # will be updated after consumer runs
        })
        mlflow.set_tags({
            "phase":  "1",
            "status": "streaming-foundation",
            "model":  "none-yet",
        })
    print("[MLflow] Baseline run logged to AWH-AnomalyDetection")


if __name__ == "__main__":
    setup_mlflow()

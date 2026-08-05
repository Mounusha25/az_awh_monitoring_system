"""
AWH Phase 3 — SensorDriftAgent

Wraps the Phase 2 Isolation Forest ensemble (research_extension/phase2_models,
current benchmark: 8 stations, scoped to temperature/humidity/weight/power,
see PENDING_TASKS.md Round 13) to decide (a) is this window anomalous, and if
so (b) which of the 4 monitored parameters is the likely cause.

Uses the saved Isolation Forest model rather than the LSTM (the higher-scoring
model, F1=0.415 as of round 14) specifically because it only needs the
window's precomputed rel_* stat columns — no raw per-timestep history lookup
— which is what a request arriving through this agent naturally carries. A
live deployment with access to each window's raw readings could swap in the
LSTM via lstm_attribution_model.py's SequenceCache mechanism instead.
"""

from __future__ import annotations

import os
import sys

import joblib
import pandas as pd

PHASE2_DIR = os.path.join(os.path.dirname(__file__), "..", "phase2_models")
sys.path.insert(0, PHASE2_DIR)

from state import DriftFinding, IncidentState  # noqa: E402

MODEL_PATH = os.path.join(PHASE2_DIR, "data", "models", "isolation_forest_ensemble.joblib")

_model = None  # lazy singleton — one load per process, not per window


def _get_model():
    global _model
    if _model is None:
        _model = joblib.load(MODEL_PATH)
    return _model


def sensor_drift_agent(state: IncidentState) -> dict:
    """LangGraph node: reads state['stat_row'], returns {'drift': DriftFinding}."""
    model = _get_model()
    row_df = pd.DataFrame([state["stat_row"]])
    preds = model.predict(row_df)

    finding: DriftFinding = {
        "is_anomaly": bool(preds["is_anomaly_pred"].iloc[0]),
        "causal_parameter": str(preds["causal_parameter_pred"].iloc[0]),
        "confidence": float(preds["detection_score"].iloc[0]),
    }
    return {"drift": finding}

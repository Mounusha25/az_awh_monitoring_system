"""
AWH Phase 3 — shared LangGraph state.

One IncidentState flows through all four agents for a single labeled window
(the same row shape train_phase2_models.py evaluates against — station_id,
window_start/end, the 4 target features' rel_* stat columns, raw readings).
Each agent only ever ADDS keys; nothing upstream is mutated, so the full
trace of who-said-what survives to the end for the incident report and the
final printed summary.
"""

from __future__ import annotations

from typing import Optional, TypedDict


class DriftFinding(TypedDict):
    is_anomaly: bool
    causal_parameter: str  # one of temperature/humidity/weight/power, or "none"
    confidence: float  # model's detection_score, 0-1


class ThresholdFinding(TypedDict):
    breached: bool
    breaches: list[dict]  # [{"parameter": ..., "value": ..., "bound": ..., "direction": "above"|"below"}]


class IncidentState(TypedDict, total=False):
    # --- input (set before the graph runs) ---
    station_id: int
    window_start: str  # ISO timestamp, for display
    window_end: str
    raw_readings: dict[str, Optional[float]]  # latest raw temperature/humidity/weight/power for this window
    stat_row: dict  # the full row of rel_* stat columns Phase 2 models expect, keyed by column name

    # --- agent outputs ---
    drift: DriftFinding
    threshold: ThresholdFinding
    incident_report: str
    escalation: dict  # {"severity": ..., "route_to": ..., "rationale": ...}

    # --- control ---
    needs_incident: bool  # set by the merge step: True if drift OR threshold flagged something

"""
AWH Phase 3 — StakeholderEscalationAgent

Decides WHO should be notified and WHY, based on the prior agents' findings.
Per explicit user decision (2026-07-31): simulated only — this logs/returns
a routing decision, it does not send anything anywhere. Wire in a real
notification channel (Slack webhook, email list, PagerDuty, etc.) only once
a real contact list/system is provided.
"""

from __future__ import annotations

from state import IncidentState


def _decide_severity(state: IncidentState) -> tuple[str, str]:
    """Returns (severity, rationale)."""
    threshold = state.get("threshold", {"breached": False, "breaches": []})
    drift = state.get("drift", {"is_anomaly": False, "causal_parameter": "none", "confidence": 0.0})

    if threshold["breached"]:
        return "high", (
            f"Reading outside physically-plausible/safety bounds: "
            f"{[b['parameter'] for b in threshold['breaches']]}."
        )
    if drift["is_anomaly"] and drift["confidence"] >= 0.7:
        return "medium", f"High-confidence anomaly attributed to {drift['causal_parameter']} (confidence {drift['confidence']:.2f})."
    if drift["is_anomaly"]:
        return "low", f"Lower-confidence anomaly attributed to {drift['causal_parameter']} (confidence {drift['confidence']:.2f})."
    return "none", "No anomaly or threshold breach detected."


# Simulated routing table — who gets notified per severity. Not a real contact
# list; swap for actual names/channels/on-call rotation when available.
ROUTE_BY_SEVERITY = {
    "high": "on-call field engineer (immediate)",
    "medium": "field engineer (next visit)",
    "low": "SSEBE lab log (no immediate action)",
    "none": "none",
}


def stakeholder_escalation_agent(state: IncidentState) -> dict:
    """LangGraph node: reads drift/threshold findings, returns {'escalation': dict}."""
    severity, rationale = _decide_severity(state)
    escalation = {
        "severity": severity,
        "route_to": ROUTE_BY_SEVERITY[severity],
        "rationale": rationale,
    }
    print(f"[StakeholderEscalationAgent] station={state['station_id']} "
          f"severity={severity} route_to='{escalation['route_to']}' — {rationale}")
    return {"escalation": escalation}

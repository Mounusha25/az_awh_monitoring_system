r"""
AWH Phase 3 — LangGraph multi-agent orchestration.

    START --> sensor_drift_agent -----> merge --> [conditional] --> incident_report_agent --> stakeholder_escalation_agent --> END
          \-> threshold_breach_agent -/                        \-> END  (nothing flagged, skip the LLM call and escalation)

SensorDriftAgent and ThresholdBreachAgent run as independent branches from
START (they read disjoint parts of the input state and don't depend on each
other); `merge` fans them back in and decides whether anything worth a
report was found at all — most windows are normal, and skipping the LLM
call entirely on those is both cheaper and matches how a real monitoring
system would behave (silence is the common case, not the exception).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from incident_report_agent import incident_report_agent
from sensor_drift_agent import sensor_drift_agent
from stakeholder_escalation_agent import stakeholder_escalation_agent
from state import IncidentState
from threshold_breach_agent import threshold_breach_agent


def _merge(state: IncidentState) -> dict:
    drift = state.get("drift", {"is_anomaly": False})
    threshold = state.get("threshold", {"breached": False})
    return {"needs_incident": bool(drift["is_anomaly"] or threshold["breached"])}


def _route_after_merge(state: IncidentState) -> str:
    return "incident_report" if state.get("needs_incident") else END


def build_graph():
    graph = StateGraph(IncidentState)

    graph.add_node("sensor_drift", sensor_drift_agent)
    graph.add_node("threshold_breach", threshold_breach_agent)
    graph.add_node("merge", _merge)
    graph.add_node("incident_report", incident_report_agent)
    graph.add_node("stakeholder_escalation", stakeholder_escalation_agent)

    graph.add_edge(START, "sensor_drift")
    graph.add_edge(START, "threshold_breach")
    graph.add_edge("sensor_drift", "merge")
    graph.add_edge("threshold_breach", "merge")
    graph.add_conditional_edges("merge", _route_after_merge, {"incident_report": "incident_report", END: END})
    graph.add_edge("incident_report", "stakeholder_escalation")
    graph.add_edge("stakeholder_escalation", END)

    return graph.compile()

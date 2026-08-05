"""
AWH Phase 3 — ThresholdBreachAgent

Per CLAUDE.md's Phase 3 spec, this agent is meant to check readings against
"EPA regulatory thresholds." AWH water-harvesting telemetry (intake/outtake
temperature and humidity, harvested-water weight, power draw) doesn't map
onto a standard EPA drinking-water contaminant limit — there's no real
regulatory citation to wire in here. Per explicit user decision (2026-07-31),
these are illustrative operational SANITY bounds instead — physically
implausible or clearly unsafe readings, not tuned per-station operational
limits and NOT a real regulatory source. Replace with real values if/when
this system needs to answer to an actual regulatory or safety framework.
"""

from __future__ import annotations

from state import IncidentState, ThresholdFinding

# (lower, upper) — a reading outside this range is either a physical
# impossibility (sensor fault) or a plausible safety concern, not a tuned
# per-station operating band.
PLACEHOLDER_BOUNDS = {
    "temperature": (0.0, 50.0),      # deg C
    "humidity": (0.0, 100.0),        # % RH — physically bounded
    "weight": (0.0, 20000.0),        # g of collected water
    "power": (0.0, 3000.0),          # W
}


def threshold_breach_agent(state: IncidentState) -> dict:
    """LangGraph node: reads state['raw_readings'], returns {'threshold': ThresholdFinding}."""
    breaches = []
    for param, (lo, hi) in PLACEHOLDER_BOUNDS.items():
        value = state["raw_readings"].get(param)
        if value is None:
            continue
        if value < lo:
            breaches.append({"parameter": param, "value": value, "bound": lo, "direction": "below"})
        elif value > hi:
            breaches.append({"parameter": param, "value": value, "bound": hi, "direction": "above"})

    finding: ThresholdFinding = {"breached": len(breaches) > 0, "breaches": breaches}
    return {"threshold": finding}

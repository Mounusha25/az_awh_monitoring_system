"""
AWH Phase 3 — IncidentReportAgent

RAG-grounded LLM incident summary: given SensorDriftAgent's and
ThresholdBreachAgent's findings for one window, retrieves relevant sections
of guides/*.md (see rag_corpus.py) and asks Claude to write a short,
non-specialist-readable summary a field engineer can act on — the RQ3
target from CLAUDE.md ("faster correct intervention decisions... vs.
numeric alert tables").

Requires Anthropic credentials resolvable by the SDK's default client
(ANTHROPIC_API_KEY env var, or `ant auth login`) — see claude-api skill.
"""

from __future__ import annotations

import anthropic

from rag_corpus import retrieve
from state import IncidentState

MODEL = "claude-opus-5"

SYSTEM_PROMPT = """You write short incident summaries for AWH (Atmospheric Water \
Harvesting) field engineers who are not data scientists. Given a flagged sensor \
window and grounding documentation, explain in plain language: what looks wrong, \
which physical system it likely traces to, and what a field engineer should \
physically go check first. Be concrete and brief — 3-5 sentences. Do not include \
statistical jargon (no "z-score", "percentile rank", "isolation forest") — \
translate findings into what a person standing at the station would observe or \
should do. If the grounding documentation doesn't directly cover the situation, \
say so rather than inventing detail."""


def _build_query_terms(state: IncidentState) -> list[str]:
    terms = []
    drift = state.get("drift")
    if drift:
        terms.append(drift["causal_parameter"])
    threshold = state.get("threshold")
    if threshold:
        terms.extend(b["parameter"] for b in threshold["breaches"])
    return terms


def _format_grounding(chunks: list[tuple[str, str]]) -> str:
    if not chunks:
        return "(No directly relevant documentation found in guides/.)"
    return "\n\n".join(f"### {title}\n{body}" for title, body in chunks)


def incident_report_agent(state: IncidentState) -> dict:
    """LangGraph node: reads drift/threshold findings + raw readings, returns
    {'incident_report': str}. Only called when needs_incident is True."""
    query_terms = _build_query_terms(state)
    grounding_chunks = retrieve(query_terms, top_k=3)
    grounding_text = _format_grounding(grounding_chunks)

    drift = state.get("drift", {"is_anomaly": False, "causal_parameter": "none", "confidence": 0.0})
    threshold = state.get("threshold", {"breached": False, "breaches": []})

    user_prompt = f"""Station: {state['station_id']}
Window: {state['window_start']} to {state['window_end']}
Raw readings: {state['raw_readings']}

SensorDriftAgent finding: {"anomaly detected" if drift["is_anomaly"] else "no anomaly"}, \
likely cause: {drift["causal_parameter"]}, confidence: {drift["confidence"]:.2f}

ThresholdBreachAgent finding: {"breach detected" if threshold["breached"] else "no breach"}
{threshold["breaches"] if threshold["breaches"] else ""}

Grounding documentation:
{grounding_text}

Write the incident summary."""

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    if response.stop_reason == "refusal":
        return {"incident_report": "(Report generation was declined by safety filtering — escalate manually.)"}

    text = next((b.text for b in response.content if b.type == "text"), "")
    return {"incident_report": text}

"""
AWH Phase 3 — RAG corpus for IncidentReportAgent.

Retrieval is simple keyword overlap over guides/*.md chunked by section
header, not an embedding/vector-DB pipeline — appropriate for a corpus this
small (~3,100 lines across 9 files) and keeps this honestly described as
"grounded in real system documentation," not a full semantic search system.
Swap in a real embedding index if the corpus grows past what keyword
overlap can usefully rank.
"""

from __future__ import annotations

import os
import re

GUIDES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "guides")

_chunks: list[tuple[str, str]] | None = None  # (section_title, section_text), loaded once


def _load_chunks() -> list[tuple[str, str]]:
    global _chunks
    if _chunks is not None:
        return _chunks

    chunks = []
    for fname in sorted(os.listdir(GUIDES_DIR)):
        if not fname.endswith(".md"):
            continue
        with open(os.path.join(GUIDES_DIR, fname), encoding="utf-8") as f:
            text = f.read()
        # split on any "#"-level header; keep the header as the chunk title
        sections = re.split(r"\n(?=#{1,3} )", text)
        for section in sections:
            lines = section.strip().splitlines()
            if not lines:
                continue
            title = f"{fname}: {lines[0].lstrip('# ').strip()}"
            body = "\n".join(lines[1:]).strip()
            if body:
                chunks.append((title, body))

    _chunks = chunks
    return chunks


def retrieve(query_terms: list[str], top_k: int = 3) -> list[tuple[str, str]]:
    """Return the top_k (title, body) chunks ranked by keyword overlap with
    query_terms (case-insensitive substring match, simple count-based score)."""
    chunks = _load_chunks()
    terms = [t.lower() for t in query_terms if t and t != "none"]
    if not terms:
        return []

    scored = []
    for title, body in chunks:
        haystack = (title + " " + body).lower()
        score = sum(haystack.count(term) for term in terms)
        if score > 0:
            scored.append((score, title, body))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [(title, body) for _, title, body in scored[:top_k]]

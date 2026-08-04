"""
Query classifier — simple (fast path) vs complex (agentic loop).

Per PRD §13.5, ~70% of queries are simple and should stay on RippleBot's existing
deterministic pipeline (zero agentic cost). Only complex / multi-source / ambiguous
queries engage the Claude loop.

This base implementation is a cheap HEURISTIC (no LLM call) so it costs nothing and
adds no latency. It can later be upgraded to a one-shot Claude Haiku classification
pass (config.ANTHROPIC_CLASSIFIER_MODEL) without changing callers.
"""
from __future__ import annotations

import re

# Signals that a query wants live/cross-source reasoning rather than a single lookup.
_LIVE_SIGNALS = re.compile(
    r"\b(how many|count|current|currently|right now|this (week|month|quarter|year)|"
    r"today|latest|live|real[- ]?time|status|active|enabled|configured|config|"
    r"compare|versus|vs\.?|difference between|both|and also|as well as)\b",
    re.IGNORECASE,
)
_HELP_SIGNALS = re.compile(r"\b(how do i|how to|how can i|set up|setup|enable|configure|what does)\b", re.IGNORECASE)


def classify_route(query: str) -> str:
    """
    Route an Assistant-tab query to the cheapest capable path:
      'help'    → pure how-to/help question with no live-data signals → help-center RAG
      'agentic' → everything else (live data, cross-source, config lookups) → tool loop
    Conservative: only takes the help fast-path when it clearly looks like a how-to
    AND shows no live-data intent.
    """
    q = (query or "").strip()
    if not q:
        return "agentic"
    if _LIVE_SIGNALS.search(q):
        return "agentic"
    if _HELP_SIGNALS.search(q):
        return "help"
    return "agentic"


def classify(query: str) -> str:
    """Return 'simple' or 'complex'. Conservative: only escalate on clear signals."""
    q = (query or "").strip()
    if not q:
        return "simple"

    words = q.split()
    # Multi-part questions (conjunctions / multiple '?') tend to be cross-source.
    if q.count("?") > 1:
        return "complex"
    if _LIVE_SIGNALS.search(q):
        return "complex"
    if _HELP_SIGNALS.search(q):
        return "complex"
    # Long, clause-heavy questions are usually multi-step.
    if len(words) > 25 and ("," in q or " and " in q.lower()):
        return "complex"
    return "simple"

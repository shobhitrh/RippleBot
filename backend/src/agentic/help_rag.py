"""
Help-center RAG fast path (PRD §16 / §18.6).

For pure how-to / help questions, skip the full agentic tool-loop: retrieve the
relevant Document360 article chunks from the shared help-center vector store
(Voyage embeddings) and synthesize a grounded, cited answer in ONE cheap model
call. Faster and cheaper than the agentic loop; live-data/cross-source questions
still go through run_agentic_stream.

Streams the same SSE contract as everything else: sources → token → done.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from backend.src import config
from backend.src.agentic.rippletools import search_knowledge_base

logger = logging.getLogger(__name__)

_SYSTEM = """You are RippleBot's help-center assistant. Answer the user's how-to / product
question using ONLY the retrieved help-center article excerpts provided. Be concise and
practical. If the excerpts don't contain the answer, say you couldn't find it in the help
center rather than guessing. Do not invent steps or settings. The UI shows source badges
separately, so don't paste raw links."""


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


async def help_rag_stream(query: str, company_id: str | None = None) -> AsyncIterator[str]:
    """Direct RAG over the shared help-center store, single-call synthesis, streamed."""
    sources: list = []
    context = search_knowledge_base(query, config.HELP_CENTER_COMPANY_ID, sources)

    # Surface citations immediately (same shape the chat UI already renders).
    yield _sse({"type": "sources", "sources": sources})

    if "No relevant passages" in context or "temporarily unavailable" in context:
        yield _sse({"type": "token", "text": "I couldn't find anything on that in the help center."})
        yield _sse({"type": "done"})
        return

    if not config.anthropic_configured():
        # No Claude key — return the retrieved snippets directly rather than nothing.
        yield _sse({"type": "token", "text": context})
        yield _sse({"type": "done"})
        return

    try:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        # Cheap model for help synthesis (grounded, single call).
        async with client.messages.stream(
            model=config.ANTHROPIC_CLASSIFIER_MODEL,
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Help-center excerpts:\n{context}\n\nQuestion: {query}"}],
        ) as stream:
            async for text in stream.text_stream:
                if text:
                    yield _sse({"type": "token", "text": text})
    except Exception as e:
        logger.error("help_rag synthesis failed: %s", e)
        # Fall back to the raw retrieved context so the user still gets an answer.
        yield _sse({"type": "token", "text": context})

    yield _sse({"type": "done"})

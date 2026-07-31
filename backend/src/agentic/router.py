"""
Agentic API surface — additive, opt-in, isolated from the existing chat pipeline.

Endpoints (all under /api/agentic):
  GET  /status  → what's configured / what keys are still missing (safe to call always)
  POST /query   → the Claude tool-use loop, SSE stream (only when AGENTIC_MODE=on)

The existing /api/chat/query endpoint is never imported or touched here, so the
current RippleBot behaviour is guaranteed unchanged.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from backend.src import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agentic", tags=["agentic"])

CompanyIdHeader = Header(default=config.DEFAULT_COMPANY_ID, alias="X-Company-Id")


class AgenticQuery(BaseModel):
    query: str
    history: list | None = None  # [{role, content}, ...]


@router.get("/status")
async def agentic_status():
    """
    Report readiness of every integration point (PRD §19.8). Lets the frontend and
    ops know exactly which keys remain to be plugged in — no secrets are returned.
    """
    return {
        "agentic_mode": config.AGENTIC_MODE,
        "orchestrator": {
            "configured": config.anthropic_configured(),
            "model": config.ANTHROPIC_MODEL,
            "classifier_model": config.ANTHROPIC_CLASSIFIER_MODEL,
            "missing": [] if config.anthropic_configured() else ["ANTHROPIC_API_KEY"],
        },
        "tools": {
            "search_knowledge_base": {"owner": "ripplebot", "ready": True},
            "exact_cell_lookup": {"owner": "ripplebot", "ready": True},
            "query_uploaded_data": {"owner": "ripplebot", "ready": True},
            "get_document_section": {"owner": "ripplebot", "ready": True},
            "query_live_database": {
                "owner": "pia",
                "ready": config.live_db_configured(),
                "missing": [] if config.live_db_configured() else ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"],
            },
            "get_tenant_configs": {
                "owner": "pia",
                "ready": config.live_db_configured(),
                "missing": [] if config.live_db_configured() else ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"],
            },
            "search_help_center": {
                "owner": "pia",
                "ready": bool(config.DOCUMENT360_API_KEY),
                "missing": [] if config.DOCUMENT360_API_KEY else ["DOCUMENT360_API_KEY"],
            },
        },
        "auth": {
            "supabase_configured": bool(config.SUPABASE_JWT_SECRET),
            "note": "Falls back to X-Company-Id header when Supabase is not configured.",
        },
    }


@router.post("/ingest-help-center")
async def ingest_help_center():
    """
    Trigger the Document360 → Voyage ingest (PRD §16). Pulls help articles and embeds
    them into the shared help-center store so search_help_center can answer. Safe to
    re-run — unchanged articles are skipped by the engine's content-hash check.
    """
    if not config.DOCUMENT360_API_KEY:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "DOCUMENT360_API_KEY not set — nothing to ingest."},
        )
    try:
        from fastapi.concurrency import run_in_threadpool
        from backend.src.agentic.ingest_d360 import ingest
        summary = await run_in_threadpool(ingest)  # network + indexing is blocking
        return JSONResponse(content=summary)
    except Exception as e:  # pragma: no cover
        logger.error("help-center ingest failed: %s", e)
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


def _msg_stream(text: str):
    import json

    async def gen():
        yield f"data: {json.dumps({'type': 'sources', 'sources': []})}\n\n"
        yield f"data: {json.dumps({'type': 'token', 'text': text})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return gen()


@router.post("/query")
async def agentic_query(payload: AgenticQuery, company_id: str = CompanyIdHeader):
    """
    Opt-in agentic endpoint. Streams SSE in the SAME shape as /api/chat/query so the
    existing chat UI can consume it. Degrades gracefully at every missing dependency.
    """
    company_id = config.normalize_company_id(company_id)
    query = (payload.query or "").strip()

    if not config.AGENTIC_MODE:
        return StreamingResponse(
            _msg_stream("The agentic engine is turned off. Set AGENTIC_MODE=on to enable it."),
            media_type="text/event-stream",
        )
    if not query:
        return StreamingResponse(_msg_stream("Please enter a question."), media_type="text/event-stream")
    if not config.anthropic_configured():
        return StreamingResponse(
            _msg_stream(
                "The agentic brain is not configured yet. Set ANTHROPIC_API_KEY in .env to "
                "activate Claude orchestration (see PRD §19.1)."
            ),
            media_type="text/event-stream",
        )

    # Import the loop lazily so a missing 'anthropic' package can't break app startup.
    try:
        from backend.src.agentic.loop import run_agentic_stream
    except Exception as e:  # pragma: no cover
        logger.error("agentic loop import failed: %s", e)
        return StreamingResponse(_msg_stream(f"Agentic engine unavailable: {e}"), media_type="text/event-stream")

    return StreamingResponse(
        run_agentic_stream(query, company_id, payload.history),
        media_type="text/event-stream",
    )

import os
import re
import time
import logging
import httpx
from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, BackgroundTasks, HTTPException, Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from backend.src import config, companies
from backend.src.rag_engine import get_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookPayload(BaseModel):
    meetingId: str
    eventType: Optional[str] = None


def sanitize_filename(name: str) -> str:
    sanitized = re.sub(r'[\\/*?:"<>|]', "_", name or "meeting")
    sanitized = re.sub(r"\s+", "_", sanitized)
    return sanitized.strip("_") or "meeting"


def format_date(date_val) -> str:
    if not date_val:
        return datetime.utcnow().strftime("%Y-%m-%d")
    try:
        if isinstance(date_val, (int, float)) or (isinstance(date_val, str) and date_val.isdigit()):
            ts = float(date_val)
            if ts > 1e11:  # milliseconds
                ts = ts / 1000.0
            return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", str(date_val))
        if match:
            return match.group(1)
    except Exception as e:
        logger.warning(f"Error parsing date {date_val}: {e}")
    return datetime.utcnow().strftime("%Y-%m-%d")


# ── Fireflies API ────────────────────────────────────────────────────────────
# Fireflies' GraphQL schema: query `transcript(id:)`, with sentences (the verbatim
# transcript) and `summary` (Fireflies' own AI — overview/action_items/etc.).
FIREFLIES_QUERY = """
query Transcript($id: String!) {
  transcript(id: $id) {
    id
    title
    dateString
    date
    duration
    organizer_email
    participants
    meeting_attendees { displayName email }
    sentences { speaker_name text }
    summary { overview action_items keywords outline }
  }
}
"""


def _mock_transcript() -> dict:
    """Local-dev fallback when FIREFLIES_API_KEY is missing/mock."""
    return {
        "id": "mock-001",
        "title": "ArgusHR System Architecture Sync",
        "dateString": datetime.utcnow().strftime("%Y-%m-%d"),
        "duration": 1800,
        "participants": ["host@company.com", "shobhit@company.com", "dev@company.com"],
        "meeting_attendees": [{"displayName": "Shobhit", "email": "shobhit@company.com"}],
        "sentences": [
            {"speaker_name": "Host", "text": "Welcome everyone, let's align on our stack."},
            {"speaker_name": "Shobhit", "text": "I propose PostgreSQL + pgvector for storage so we can host in the cloud."},
            {"speaker_name": "Dev", "text": "One doubt: will the 800-token chunk size hold for wide Excel sheets?"},
            {"speaker_name": "Host", "text": "Good question — we'll verify. Action: Dev to create the Python backend by July."},
        ],
        "summary": {
            "overview": "The team aligned on using PostgreSQL with pgvector for cloud-hosted vector storage.",
            "action_items": "Dev to create the Python backend by July.\nVerify chunk size for wide Excel sheets.",
            "keywords": ["pgvector", "chunking", "backend"],
            "outline": "Stack decision; chunking concern; action items.",
        },
    }


async def fetch_transcript(meeting_id: str) -> dict:
    """Fetch a transcript + Fireflies' native summary from the Fireflies API."""
    key = config.FIREFLIES_API_KEY
    if not key or key.lower() in ("mock", "test", ""):
        logger.info("Fireflies: API key missing/mock — returning mock transcript.")
        return _mock_transcript()

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.fireflies.ai/graphql",
            json={"query": FIREFLIES_QUERY, "variables": {"id": meeting_id}},
            headers=headers,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Fireflies API status {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if data.get("errors"):
            raise HTTPException(status_code=502, detail=f"Fireflies GraphQL error: {data['errors'][0].get('message')}")
        t = (data.get("data") or {}).get("transcript")
        if not t:
            raise HTTPException(status_code=404, detail=f"Transcript {meeting_id} not found on Fireflies")
        return t


def build_meeting_markdown(t: dict) -> tuple[str, str, str]:
    """
    Build the meeting markdown: Fireflies' AI summary + action items on top, then
    the FULL speaker-labeled transcript (this is what makes RAG lossless).
    Returns (filename_stub, date_str, markdown).
    """
    title = t.get("title") or "Untitled Meeting"
    meeting_id = t.get("id") or ""
    date_str = format_date(t.get("dateString") or t.get("date"))
    participants = t.get("participants") or [
        a.get("email") or a.get("displayName") for a in (t.get("meeting_attendees") or [])
    ]
    # Fireflies returns duration in MINUTES (not seconds).
    duration = t.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        m = round(duration)
        duration_str = f"{m // 60}h {m % 60}m" if m >= 60 else f"{m} min"
    else:
        duration_str = "N/A"

    summary = t.get("summary") or {}
    overview = (summary.get("overview") or "").strip()
    action_items = summary.get("action_items")
    keywords = summary.get("keywords")
    outline = summary.get("outline")

    # Full transcript, speaker-labeled — the lossless core for "answer anything".
    lines = []
    for s in (t.get("sentences") or []):
        spk = (s.get("speaker_name") or "Speaker").strip()
        txt = (s.get("text") or "").strip()
        if txt:
            lines.append(f"{spk}: {txt}")
    transcript_text = "\n".join(lines)

    def _fmt_list(v):
        if isinstance(v, list):
            return "\n".join(f"- {x}" for x in v if str(x).strip())
        if isinstance(v, str):
            return "\n".join(f"- {x.strip()}" for x in v.splitlines() if x.strip())
        return ""

    parts = [
        f"# {title}",
        "",
        "## Executive Summary",
        overview or "_No summary provided by Fireflies._",
    ]
    ai = _fmt_list(action_items)
    if ai:
        parts += ["", "## Action Items", ai]
    ol = outline if isinstance(outline, str) else _fmt_list(outline)
    if ol and ol.strip():
        parts += ["", "## Outline", ol]
    kw = ", ".join(keywords) if isinstance(keywords, list) else (keywords or "")
    if kw:
        parts += ["", f"**Keywords:** {kw}"]
    parts += ["", "## Full Transcript", transcript_text or "_Empty transcript._"]
    body = "\n".join(parts)

    # YAML frontmatter (drives per-tenant metadata + Meeting Logs display).
    fm = (
        "---\n"
        f"title: {title}\n"
        f"meeting_id: {meeting_id}\n"
        f"date: {date_str}\n"
        f"participants: {participants}\n"
        f"duration: {duration_str}\n"
        f"source: fireflies\n"
        f"uploaded_by: Fireflies\n"
        f"category: Meeting\n"
        "---\n\n"
    )
    return sanitize_filename(title), date_str, fm + body


def _already_indexed(docs_dir: str, meeting_id: str) -> bool:
    """Idempotency: skip if a file for this meeting_id already exists (webhook retries)."""
    if not meeting_id or not os.path.isdir(docs_dir):
        return False
    for f in os.listdir(docs_dir):
        if not f.startswith("FF_") or not f.endswith(".md"):
            continue
        try:
            with open(os.path.join(docs_dir, f), "r", encoding="utf-8") as fh:
                head = fh.read(600)
            if f"meeting_id: {meeting_id}" in head:
                return True
        except Exception:
            continue
    return False


def _attendee_emails(t: dict) -> list:
    """All emails on a transcript — for domain-based company routing."""
    emails = list(t.get("participants") or [])
    if t.get("organizer_email"):
        emails.append(t["organizer_email"])
    for a in (t.get("meeting_attendees") or []):
        if a.get("email"):
            emails.append(a["email"])
    return emails


async def process_meeting(meeting_id: str, company_id: str = None):
    """
    Fetch → (resolve tenant by attendee email domain if not given) → build markdown
    → save to that company's KB → index (Voyage embeddings).

    Auto-routed meetings that DON'T match a known company domain are DISCARDED
    (not ingested) — the workspace has lots of internal/scrum calls we don't want
    to store or review. Only meetings with a recognized client domain are kept.
    Explicit imports (Import-by-ID / the /{company_id} route) always ingest.
    """
    try:
        transcript = await fetch_transcript(meeting_id)

        auto = not company_id
        if auto:
            company_id = companies.resolve_company_from_emails(_attendee_emails(transcript))
            if company_id == companies.UNASSIGNED_ID:
                logger.info(
                    f"Fireflies: meeting {meeting_id} has no known client domain — discarded "
                    f"(attendees: {_attendee_emails(transcript)[:5]})"
                )
                return
            logger.info(f"Fireflies: routed meeting {meeting_id} → tenant '{company_id}' by email domain")
        else:
            company_id = config.normalize_company_id(company_id)

        docs_dir = config.company_documents_dir(company_id)
        if _already_indexed(docs_dir, meeting_id):
            logger.info(f"Fireflies: meeting {meeting_id} already present for '{company_id}' — skipping.")
            return

        stub, date_str, markdown = build_meeting_markdown(transcript)

        filename = f"FF_{stub}_{date_str}.md"
        filepath = os.path.join(docs_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(markdown)
        logger.info(f"Fireflies: saved {filepath}")

        # Back the meeting file up to durable storage (survives ephemeral-disk wipe).
        try:
            from backend.src import file_store
            file_store.mirror_dir(company_id, docs_dir)
        except Exception as e:
            logger.error(f"Fireflies: file_store mirror failed: {e}")

        # Index into this tenant's vector store (blocking → run off the event loop).
        engine = get_engine(company_id, required=False)
        if engine is not None:
            await run_in_threadpool(engine.build_index, False)
            logger.info(f"Fireflies: indexed meeting {meeting_id} for '{company_id}'")
        else:
            logger.warning("Fireflies: vector store unavailable — file saved, will index later.")
    except Exception:
        logger.error(f"Fireflies: processing failed for meeting {meeting_id}", exc_info=True)


async def _verify(request: "Request", token: Optional[str], header_token: Optional[str]):
    """
    Authenticate the webhook. Accepts EITHER:
      • the shared secret as ?token= / X-Webhook-Token, OR
      • Fireflies' HMAC-SHA256 signature (the "Signing Secret" field) sent in
        x-hub-signature / x-hub-signature-256, computed over the raw body.
    Both use FIREFLIES_WEBHOOK_SECRET. If no secret is configured, allow (open).
    """
    import hmac, hashlib
    secret = config.FIREFLIES_WEBHOOK_SECRET
    if not secret:
        return
    if token == secret or header_token == secret:
        return
    # HMAC signature path
    sig = (
        request.headers.get("x-hub-signature-256")
        or request.headers.get("x-hub-signature")
        or request.headers.get("x-fireflies-signature")
        or ""
    ).split("=")[-1].strip()
    if sig:
        raw = await request.body()
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, sig):
            return
    raise HTTPException(status_code=401, detail="Webhook authentication failed")


def _should_process(event_type: Optional[str]) -> bool:
    """Process transcription/summary-ready events; ignore others (e.g. bot joined)."""
    e = (event_type or "").lower()
    if not e:
        return True  # manual test posts without an eventType
    return "transcri" in e or "summ" in e


async def _parse_event(request: Request) -> tuple:
    """
    Leniently extract (meeting_id, event_type) from whatever JSON Fireflies sends.
    Fireflies' test pings and real events vary in shape, and a strict schema causes
    422s — so we accept any body and probe common keys / nesting.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    # meeting/transcript id under several possible keys, incl. nested data/meeting
    nested = {}
    for k in ("data", "meeting", "transcript", "payload"):
        v = body.get(k)
        if isinstance(v, dict):
            nested.update(v)
    src = {**nested, **body}
    meeting_id = (
        src.get("meetingId") or src.get("meeting_id") or src.get("transcriptId")
        or src.get("transcript_id") or src.get("id")
    )
    event_type = src.get("eventType") or src.get("event_type") or src.get("event") or src.get("type")
    return meeting_id, event_type


@router.post("/fireflies/{company_id}")
async def fireflies_webhook(
    request: Request,
    company_id: str,
    background_tasks: BackgroundTasks,
    token: Optional[str] = Query(default=None),
    x_webhook_token: Optional[str] = Header(default=None),
):
    """Fireflies webhook scoped to an explicit company (override of domain routing)."""
    await _verify(request, token, x_webhook_token)
    company_id = config.normalize_company_id(company_id)
    meeting_id, event_type = await _parse_event(request)
    if not meeting_id:
        return {"status": "ok", "message": "No meetingId in payload (test ping received)."}
    if not _should_process(event_type):
        return {"status": "ignored", "eventType": event_type}
    logger.info(f"Fireflies webhook (explicit tenant='{company_id}'): meetingId={meeting_id} event={event_type}")
    background_tasks.add_task(process_meeting, meeting_id, company_id)
    return {"status": "processing", "company_id": company_id, "meeting_id": meeting_id}


@router.post("/fireflies")
async def fireflies_webhook_autoroute(
    request: Request,
    background_tasks: BackgroundTasks,
    token: Optional[str] = Query(default=None),
    x_webhook_token: Optional[str] = Header(default=None),
):
    """
    Single webhook for one Fireflies account serving many companies. The tenant is
    resolved from attendee email domains; unmatched meetings go to 'unassigned'.
    """
    await _verify(request, token, x_webhook_token)
    meeting_id, event_type = await _parse_event(request)
    if not meeting_id:
        return {"status": "ok", "message": "No meetingId in payload (test ping received)."}
    if not _should_process(event_type):
        return {"status": "ignored", "eventType": event_type}
    logger.info(f"Fireflies webhook (auto-route): meetingId={meeting_id} event={event_type}")
    background_tasks.add_task(process_meeting, meeting_id, None)
    return {"status": "processing", "routing": "by-domain", "meeting_id": meeting_id}

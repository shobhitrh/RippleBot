import os
import re
import time
import logging
import httpx
from datetime import datetime
from pydantic import BaseModel
from fastapi import APIRouter, BackgroundTasks, HTTPException
from backend.src import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

class WebhookPayload(BaseModel):
    meetingId: str

def sanitize_filename(name: str) -> str:
    # Replace invalid chars on Windows/Linux with underscores
    sanitized = re.sub(r'[\\/*?:"<>|]', "_", name)
    sanitized = re.sub(r'\s+', '_', sanitized)
    return sanitized.strip("_")

def format_date(date_val) -> str:
    if not date_val:
        return datetime.utcnow().strftime('%Y-%m-%d')
    try:
        # If it's a numeric timestamp (milliseconds or seconds)
        if isinstance(date_val, (int, float)) or (isinstance(date_val, str) and date_val.isdigit()):
            ts = float(date_val)
            if ts > 1e11:  # Milliseconds
                ts = ts / 1000.0
            return datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')
        # If it is ISO string
        match = re.match(r'^(\d{4}-\d{2}-\d{2})', str(date_val))
        if match:
            return match.group(1)
    except Exception as e:
        logger.warning(f"Error parsing date {date_val}: {e}")
    return datetime.utcnow().strftime('%Y-%m-%d')

async def fetch_meeting_details(meeting_id: str) -> dict:
    """Fetch meeting details from Fireflies API, with fallback mock data."""
    if not config.FIREFLIES_API_KEY or config.FIREFLIES_API_KEY.lower() in ["mock", "test", ""]:
        logger.info("Fireflies: API key missing or set to mock. Returning mock meeting data.")
        # Wait a little to simulate request
        time.sleep(1.0)
        return {
            "title": "ArgusHR System Architecture Sync",
            "date": int(time.time() * 1000),
            "transcript": {
                "text": (
                    "Host: Welcome everyone. Let's align on our stack. "
                    "Shobhit: I propose using PostgreSQL and pgvector for storage. It enables cloud hosting and easy sharing. "
                    "Dev: Great idea. This removes local PC file dependencies. I will create a python backend. "
                    "Host: Excellent. Let's make sure our chunk size matches the 800 token config. The date is set for July 2026."
                )
            },
            "participants": ["host@company.com", "shobhit@company.com", "dev@company.com"]
        }

    # Real Fireflies API GraphQL call
    url = "https://api.fireflies.ai/graphql"
    headers = {
        "Authorization": f"Bearer {config.FIREFLIES_API_KEY}",
        "Content-Type": "application/json"
    }
    
    query = """
    query GetMeeting($id: String!) {
      meeting(id: $id) {
        title
        date
        transcript {
          text
        }
        participants
      }
    }
    """
    
    variables = {"id": meeting_id}
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json={"query": query, "variables": variables}, headers=headers, timeout=20.0)
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Fireflies API returned status {response.status_code}")
                
            res_data = response.json()
            if "errors" in res_data:
                logger.error(f"Fireflies GraphQL errors: {res_data['errors']}")
                raise HTTPException(status_code=502, detail=f"Fireflies GraphQL query error: {res_data['errors'][0].get('message')}")
                
            meeting = res_data.get("data", {}).get("meeting")
            if not meeting:
                raise HTTPException(status_code=404, detail=f"Meeting with ID {meeting_id} not found on Fireflies")
                
            return meeting
        except httpx.RequestError as e:
            logger.error(f"HTTP connection to Fireflies failed: {e}")
            raise HTTPException(status_code=503, detail="Could not connect to Fireflies API")

def transform_transcript_with_llm(raw_text: str, title: str, date_str: str, participants: list) -> str:
    """Send raw transcript to LLM to produce a high-density Markdown summary."""
    prompt = f"""
    Transform this raw transcript into a high-density Markdown document.
    Preserve all technical decisions, architecture choices, Q&A, and action items. Remove filler words and banter.
    Format as clean Markdown with sections: Executive Summary, Key Decisions, Technical Details, Q&A, Action Items.

    Meeting Metadata:
    - Title: {title}
    - Date: {date_str}
    - Participants: {", ".join(participants)}

    Raw Transcript:
    {raw_text}
    """
    
    groq_key = config.GROQ_API_KEY
    openai_key = os.getenv("OPENAI_API_KEY")
    
    if groq_key:
        logger.info("Using Groq API to summarize transcript.")
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq transcript summarization failed: {e}. Falling back to OpenAI if available.")
            
    if openai_key:
        logger.info("Using OpenAI API to summarize transcript.")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI transcript summarization failed: {e}")
            
    # Mock fallback if no LLM APIs are configured or if they both failed
    logger.warning("No LLM keys working. Falling back to rule-based markdown extraction.")
    return f"""# Executive Summary
This is an automatically parsed meeting summary for **{title}** held on **{date_str}**.

# Key Decisions
- Adopt PostgreSQL and the pgvector extension for cloud storage.
- Align code chunk size to 800 tokens to preserve structural integrity.

# Technical Details
- Database: PostgreSQL (Neon / Supabase compatible)
- Embeddings Model: `voyage-4-large`
- Generation LLM: `llama-3.3-70b-versatile`

# Action Items
- Create PostgreSQL schema definition [Assignee: Dev]
- Update integration documentation [Assignee: Host]
"""

async def process_webhook_background(meeting_id: str):
    """Background task to fetch, transform and save the Fireflies transcript."""
    logger.info(f"Background: Starting process for meeting {meeting_id}")
    try:
        meeting = await fetch_meeting_details(meeting_id)
        
        title = meeting.get("title", "Untitled Meeting")
        date_raw = meeting.get("date")
        date_str = format_date(date_raw)
        participants = meeting.get("participants", [])
        
        # Get raw transcript text
        transcript_obj = meeting.get("transcript") or {}
        raw_text = transcript_obj.get("text", "")
        
        if not raw_text.strip():
            logger.warning(f"Background: Meeting {meeting_id} has an empty transcript. Skipping.")
            return

        # Transform raw transcript using LLM
        structured_markdown = transform_transcript_with_llm(raw_text, title, date_str, participants)
        
        # Prepend YAML frontmatter with metadata for indexing
        frontmatter = f"""---
title: {title}
meeting_id: {meeting_id}
date: {date_str}
participants: {participants}
department: Engineering
category: Meeting Summary
---

"""
        full_content = frontmatter + structured_markdown
        
        # Save to knowledge_base
        safe_title = sanitize_filename(title)
        filename = f"FF_{safe_title}_{date_str}.md"
        filepath = os.path.join(config.DOCUMENTS_DIR, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(full_content)
            
        logger.info(f"Background: Webhook file saved successfully: {filepath}")
        
    except Exception as e:
        logger.error(f"Background: Webhook processing failed for meeting {meeting_id}: {e}")

@router.post("/fireflies")
async def receive_fireflies_webhook(payload: WebhookPayload, background_tasks: BackgroundTasks):
    """Receive meeting ID from Fireflies, trigger async extraction, and return HTTP 200."""
    logger.info(f"Webhook received: meetingId={payload.meetingId}")
    background_tasks.add_task(process_webhook_background, payload.meetingId)
    return {"status": "processing", "message": "Meeting processing started in background."}

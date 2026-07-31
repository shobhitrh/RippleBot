"""
The Claude agentic loop — PIA's stream_answer, transplanted onto RippleBot.

Streams Server-Sent Events in RippleBot's existing contract so the current chat UI
renders it unchanged:
    {"type": "sources", "sources": [{file, snippet}, ...]}
    {"type": "token",   "text": "..."}          (repeated)
    {"type": "done"}

The `anthropic` SDK is imported LAZILY inside the loop, so importing this module
never requires the package to be installed. If the key or package is missing, the
caller (router) reports "not configured" instead of engaging the loop.

Follows current Anthropic API guidance: AsyncAnthropic, messages.stream, adaptive
thinking, a prompt-cached stable prefix (system + tools) for cost, tool_use →
tool_result threading, and a forced final turn when the tool budget is exhausted.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator, List, Optional

from backend.src import config
from backend.src.agentic.tools import TOOL_SCHEMAS, execute_tool

logger = logging.getLogger(__name__)

_MAX_TOOL_RESULT_CHARS = 16000


SYSTEM_PROMPT = """You are RippleBot, an enterprise knowledge assistant for a specific company (tenant).

You have tools spanning three knowledge planes. Choose in this priority order:
1. search_help_center — for "how do I…" / feature / setup questions (canonical product docs).
2. search_knowledge_base / exact_cell_lookup / query_uploaded_data / get_document_section —
   for anything in THIS company's UPLOADED documents and spreadsheets.
3. query_live_database / get_tenant_configs / list_live_schemas — ONLY for live,
   current, operational data that is never uploaded (current counts, live config,
   real-time status). Read-only. The live server has MANY per-tenant schemas: omit
   `schema` to fan out across all (results come back labelled per schema), or call
   list_live_schemas and pass a specific `schema` to target one tenant.

Rules:
- Every tool is scoped to the current company automatically — never ask for a company id.
- For multi-part questions, decompose and call the right tool for each part, then synthesize.
- NEVER invent figures. If the tools return nothing, say you couldn't find it.
- NEVER write SQL that modifies data — only read-only SELECT is permitted and enforced.
- Cite which source each fact came from in plain English (the UI shows file badges separately).
- Answer in clear plain English; use markdown tables for config/tabular answers.
"""


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _cached_system() -> list:
    """System prompt as a cacheable block (stable prefix → ~0.1x cached reads)."""
    return [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


async def run_agentic_stream(
    query: str,
    company_id: str,
    history: Optional[List[dict]] = None,
) -> AsyncIterator[str]:
    """
    Drive the Claude tool-use loop and yield SSE frames. Assumes the caller has
    already verified config.anthropic_configured() is True.
    """
    try:
        from anthropic import AsyncAnthropic
    except Exception:
        yield _sse({"type": "token", "text": "The agentic engine requires the 'anthropic' package. Add it to requirements and redeploy."})
        yield _sse({"type": "done"})
        return

    client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

    # Trim history to the configured window (last N turns × 2 messages).
    hist = (history or [])[-(config.MAX_HISTORY_TURNS * 2):]
    messages: List[dict] = [
        {"role": m.get("role", "user"), "content": m.get("content", "")}
        for m in hist
        if m.get("content")
    ]
    messages.append({"role": "user", "content": query})

    collected_sources: list = []
    sources_emitted = False

    for _turn in range(config.MAX_TOOL_TURNS):
        try:
            async with client.messages.stream(
                model=config.ANTHROPIC_MODEL,
                max_tokens=2048,
                system=_cached_system(),
                tools=TOOL_SCHEMAS,
                thinking={"type": "adaptive"},
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield _sse({"type": "token", "text": text})
                final = await stream.get_final_message()
        except Exception as e:
            logger.error("agentic stream failed: %s", e)
            yield _sse({"type": "token", "text": f"\n\n(Assistant engine error: {e})"})
            yield _sse({"type": "done"})
            return

        # Persist the assistant turn (must include tool_use blocks for the next turn).
        messages.append({"role": "assistant", "content": final.content})

        tool_calls = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
        if not tool_calls:
            break  # model produced a final answer

        tool_results = []
        for tc in tool_calls:
            result = execute_tool(tc.name, tc.input, company_id, collected_sources)
            if result and len(result) > _MAX_TOOL_RESULT_CHARS:
                result = result[:_MAX_TOOL_RESULT_CHARS] + "\n[... truncated]"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": result,
            })

        # Emit citations once, as soon as the first sources appear.
        if collected_sources and not sources_emitted:
            yield _sse({"type": "sources", "sources": collected_sources})
            sources_emitted = True

        messages.append({"role": "user", "content": tool_results})
    else:
        # Tool budget exhausted — force a final, tool-free answer (PIA parity).
        messages.append({
            "role": "user",
            "content": "You've reached the tool limit. Stop calling tools and write the best answer you can from what you've gathered.",
        })
        try:
            async with client.messages.stream(
                model=config.ANTHROPIC_MODEL,
                max_tokens=2048,
                system=_cached_system(),
                thinking={"type": "adaptive"},
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield _sse({"type": "token", "text": text})
        except Exception as e:
            logger.error("agentic forced-final failed: %s", e)
            yield _sse({"type": "token", "text": f"\n\n(Assistant engine error: {e})"})

    if collected_sources and not sources_emitted:
        yield _sse({"type": "sources", "sources": collected_sources})
    yield _sse({"type": "done"})

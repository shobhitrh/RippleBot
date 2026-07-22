import os
import re
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel
from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from fastapi.concurrency import run_in_threadpool
from backend.src import config
from backend.src import companies
from backend.src import table_store
from backend.src.rag_engine import get_engine
from backend.src.excel_parser import sanitize_name

def _persona(company_display: str) -> str:
    """Shared system-prompt preamble that locks the assistant to one customer
    and to a technical/configuration (not sales/marketing) posture."""
    return f"""You are RippleBot, a technical & configuration specialist dedicated exclusively to the customer "{company_display}".

SCOPE — CUSTOMER ISOLATION (non-negotiable):
- Every answer must be about "{company_display}" and grounded ONLY in the provided context, which contains ONLY this customer's documents and meetings.
- Never mention, compare to, or draw on any other customer or company. Do not speculate about how other customers do things.
- Speak as if "{company_display}" is the only customer that exists.

ANSWER STYLE — TECHNICAL, NOT SALES:
- Give customer-level technical and configuration detail: specific config values, setup steps, integration specifics, environment/parameter names, decisions and commitments recorded in this customer's docs and meetings.
- Do NOT give generic product overviews, marketing, or sales framing. Prefer the concrete "how it is configured for {company_display}" over "what the product can do in general"."""




logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

NO_ANSWER = "I couldn't find this information in our meeting archives or knowledge base."

def _sse(obj: dict) -> str:
    """Encode a dict as one Server-Sent-Events frame."""
    return f"data: {json.dumps(obj)}\n\n"

async def _single_message_stream(text: str, sources: list = None):
    """Emit a sources frame, one token frame, and done — used for graceful states."""
    yield _sse({"type": "sources", "sources": sources or []})
    yield _sse({"type": "token", "text": text})
    yield _sse({"type": "done"})

class QueryFilters(BaseModel):
    department: Optional[str] = None
    date_from: Optional[str] = None  # Expects YYYY-MM-DD

class QueryPayload(BaseModel):
    query: str
    filters: Optional[QueryFilters] = None

async def route_and_execute(query_text: str, company_id: str = None) -> Optional[dict]:
    """
    Query-Time Router: Classify query dynamically using LLM.
    If it requires SQL (count, average, sum, min, max, global filters),
    it generates the exact SQL query, runs it against this tenant's Tier-C tables
    (Postgres or SQLite, via table_store), and formats the results to bypass
    semantic search and guarantee no data loss.
    """
    try:
        schema_map = table_store.get_router_schema(company_id)
    except Exception as e:
        logger.error(f"Failed to read Tier-C schema: {e}")
        return None
    if not schema_map:
        return None

    # Two-Stage Routing: if schema has > 15 tables, ask LLM to shortlist top 3 candidate tables first
    if len(schema_map) > 15:
        table_overview = ""
        for t, info in schema_map.items():
            title = info.get("title")
            title_str = f" (Title: \"{title}\")" if title else ""
            table_overview += f"- {t}{title_str}\n"

        shortlist_prompt = f"""You are a database table selector. Given the user's query and a list of available database tables, select up to 3 table names that are most relevant to answering the query.

Available Tables:
{table_overview}

User Query: {query_text}

JSON Output format: {{"candidates": ["table_name_1", "table_name_2"]}}
Output ONLY valid JSON."""
        
        candidates = []
        for gkey in config.GROQ_API_KEYS:
            try:
                from groq import Groq
                client = Groq(api_key=gkey)
                res = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": shortlist_prompt}],
                    temperature=0.0,
                    max_tokens=100
                )
                txt = res.choices[0].message.content or ""
                if "{" in txt:
                    txt = txt[txt.find("{"):txt.rfind("}")+1]
                    cdata = json.loads(txt)
                    candidates = cdata.get("candidates", [])
                    if candidates:
                        break
            except Exception:
                continue

        if candidates:
            filtered_schema = {t: schema_map[t] for t in candidates if t in schema_map}
            if filtered_schema:
                schema_map = filtered_schema

    # Construct schema description for the router prompt.
    schema_desc = ""
    for t, info in schema_map.items():
        title = info.get("title")
        title_str = f" (Table Title: \"{title}\")" if title else ""
        schema_desc += f"Table: {t}{title_str}\nColumns:\n"
        for c in info.get("columns", []):
            samples = c.get("samples") or []
            sample_str = f" [Sample values: {', '.join(samples)}]" if samples else ""
            schema_desc += f"  - {c['name']} ({c['type']}){sample_str}\n"
        schema_desc += "\n"

    # Cap total schema prompt size to 10,000 characters
    if len(schema_desc) > 10000:
        schema_desc = schema_desc[:10000] + "\n... [schema truncated]\n"

    system_msg = """You are an SQL routing agent. Your job is to analyze the user's query and decide if it requires querying the tenant's data tables (SQL) or doing a semantic text search (VECTOR).

Use {"route": "SQL", "sql_query": "..."} ONLY for questions that specifically require quantitative data analysis from tables: metrics, numbers, counts, aggregations, exact date lookups, mandays, fees, pricing options, or checking specific table cell values based on conditions.
Use {"route": "VECTOR"} for questions that require explaining concepts, describing what something consists of, summarization, general knowledge from the documents, or if the question is conversational (e.g. "hi", "hello", "thanks").

EXAMPLES:

Example 1 (Cell Lookup for Row Metric):
User Query: "What is the growth rate for Q3 in the financial report?"
Output: {"route": "SQL", "sql_query": "SELECT q3_growth FROM quarterly_metrics_table_1 WHERE LOWER(col_0) LIKE '%growth%'"}

Example 2 (Pure Count Query):
User Query: "How many tickets have high priority?"
Output: {"route": "SQL", "sql_query": "SELECT COUNT(*) FROM support_tickets_table_1 WHERE LOWER(priority) = LOWER('high')"}

Example 3 (List and Count Query):
User Query: "How many questions have medium criticality? What are they?"
Output: {"route": "SQL", "sql_query": "SELECT id, requirement, criticality FROM questions_table_1 WHERE LOWER(criticality) = LOWER('medium')"}

RULES:
1. ONLY write SELECT queries. Never write write/update queries.
2. Only reference tables and columns EXACTLY as defined in the schema.
3. CRITICAL: string comparisons are case-sensitive when using '='. You MUST always use LOWER(column) LIKE '%value%' or LOWER(column) = LOWER('value') for any string comparison filters to avoid case mismatch errors.
4. CRITICAL INSTRUCTION FOR ROW METRICS: Look at the sample values for col_0 in each table schema. If col_0 contains metric/row names such as 'YoY', 'Manday', 'Total', 'Total License Fees', 'Year 1', 'Year 2', etc., and the user asks for one of these metrics or line items (such as 'yoy' or 'yoy rate'), you MUST select that row directly using: SELECT <column> FROM <table> WHERE LOWER(col_0) LIKE '%metric%'. DO NOT perform percentage difference formulas like (col2 - col1) / col1.
5. CRITICAL: NEVER combine aggregate functions (e.g. COUNT(*), SUM()) with un-aggregated column names in the same SELECT statement without a GROUP BY (e.g. NEVER write SELECT COUNT(id), id). If the user asks both "how many" and "what are they", SELECT the matching rows directly (e.g. SELECT id, requirement, criticality FROM ...); the downstream assistant will count the rows automatically.
6. Output ONLY the JSON block, no markdown, no other text.
"""

    prompt = f"""Available Database Schema:
{schema_desc}

User Query: {query_text}

JSON Output:"""

    response_text = None
    for gkey in config.GROQ_API_KEYS:
        try:
            from groq import Groq
            client = Groq(api_key=gkey)
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=300
            )
            response_text = completion.choices[0].message.content
            logger.info(f"LLM Router Response: {response_text}")
            if response_text:
                break
        except Exception:
            continue
            
    if not response_text and config.GEMINI_API_KEY:
        try:
            import google.generativeai as genai
            genai.configure(api_key=config.GEMINI_API_KEY)
            model = genai.GenerativeModel("gemini-2.0-flash", system_instruction=system_msg)
            completion = model.generate_content(prompt)
            response_text = completion.text
        except Exception:
            pass
            
    if not response_text:
        return None
        
    try:
        clean_json = response_text.strip()
        if "```" in clean_json:
            clean_json = re.sub(r'^```[a-zA-Z]*\n?|```$', '', clean_json, flags=re.MULTILINE).strip()
        
        json_match = re.search(r'\{.*\}', clean_json, re.DOTALL)
        if json_match:
            clean_json = json_match.group(0)
            
        try:
            data = json.loads(clean_json, strict=False)
        except Exception:
            fixed_json = re.sub(r"\\'", "'", clean_json)
            fixed_json = re.sub(r'\\([^"\\/bfnrtu])', r'\1', fixed_json)
            data = json.loads(fixed_json, strict=False)
        if data.get("route") == "SQL" and data.get("sql_query"):
            sql = data["sql_query"]
            logger.info(f"Query-Time Router selected SQL path: {sql}")
            
            # Execute against this tenant's Tier-C tables (Postgres or SQLite).
            col_names, rows = table_store.execute_select(sql, company_id)
            
            # Format results as a neat markdown table
            is_empty = True
            if rows:
                for r in rows:
                    if any(v is not None and str(v).strip() != "" and str(v).lower() != "none" for v in r):
                        is_empty = False
                        break
            
            if not rows or is_empty:
                logger.info(f"SQL query returned 0 rows or only null values. Falling back to vector search. SQL: {sql}")
                return None
                
            results_md = f"SQL Query executed: `{sql}`\n\n"
            results_md += "| " + " | ".join(col_names) + " |\n"
            results_md += "| " + " | ".join(["---"] * len(col_names)) + " |\n"
            for r in rows[:40]:
                results_md += "| " + " | ".join(str(v) for v in r) + " |\n"
                    
            # Match tables inside the SQL query to their source files for UI citations
            matched_files = []
            sql_lower = sql.lower()
            docs_dir = config.company_documents_dir(company_id)
            listing = os.listdir(docs_dir) if os.path.isdir(docs_dir) else []
            for t_name, info in schema_map.items():
                if t_name.lower() in sql_lower:
                    source_key = (info.get("source_key") or t_name).lower()
                    for f in listing:
                        if sanitize_name(f) in source_key:
                            matched_files.append(f)
                            break
                            
            sources = []
            for f in set(matched_files):
                sources.append({
                    "filename": f,
                    "relative_path": f"./backend/knowledge_base/{f}",
                    "exact_snippet_text": f"SQL Query: {sql}\n\nResults:\n{results_md}",
                    "score": 1.0
                })
                
            return {
                "route": "SQL",
                "sql_query": sql,
                "results_markdown": results_md,
                "sources": sources
            }
    except Exception as e:
        logger.error(f"SQL Router execution failed: {e}. Falling back to semantic search.")
        
    return None

@router.post("/query")
async def chat_query(
    payload: QueryPayload,
    company_id: str = Header(default=config.DEFAULT_COMPANY_ID, alias="X-Company-Id"),
):
    """
    Retrieve relevant chunks from the configured vector store (backend-agnostic:
    ChromaDB or pgvector), scoped to the requesting company, then stream an
    onboarding-assistant answer. Degrades gracefully — never 500s.
    """
    company_id = config.normalize_company_id(company_id)
    company_display = companies.company_name(company_id)
    query_text = (payload.query or "").strip()
    if not query_text:
        return StreamingResponse(
            _single_message_stream("Please enter a question."),
            media_type="text/event-stream",
        )

    # Detect if query is a simple greeting or general assistant request
    clean_query = re.sub(r"[^\w\s]", "", query_text.lower()).strip()
    greetings = {
        "hi",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "greetings",
        "yo",
        "sup",
        "hola",
        "namaste",
    }
    is_conversational = clean_query in greetings or clean_query in {
        "who are you",
        "what can you do",
        "help",
        "who is this",
        "what is your name",
    }

    # 1. Attempt Query-Time SQL Routing (Tier C) for calculations and aggregations
    sql_result = None
    if not is_conversational:
        try:
            sql_result = await route_and_execute(query_text, company_id)
        except Exception as e:
            logger.error(f"Error in SQL query routing: {e}")

    if sql_result:
        # Stream using SQL results as context block
        sources = sql_result["sources"]
        
        async def sql_chat_stream_generator():
            yield _sse({"type": "sources", "sources": sources})
            
            context_block = f"[Database Query Results]\n{sql_result['results_markdown']}"
            system_prompt = _persona(company_display) + """

CRITICAL INSTRUCTIONS ON SOURCES AND CITATIONS:
- NEVER include inline source citations, file names, dates, or source links in your response text (e.g. do NOT write things like '[Source: file.xlsx]', 'Source: ...', or reference filenames in parentheses).
- The user interface automatically displays clickable file badges at the bottom of your response based on the returned sources list.

CRITICAL INSTRUCTIONS ON COUNTING AND SUMMARIES:
- You have been provided with raw SQL database results answering the user's question.
- Rely 100% on the SQL query results to state counts, averages, lists, or filters. They are exact and mathematically correct.
- State the final answer clearly and directly in a human-friendly format.
"""
            user_prompt = f"""Context Snippets:
{context_block}

Question: {query_text}

Answer:"""
            
            streamed = False
            for i, gkey in enumerate(config.GROQ_API_KEYS, 1):
                try:
                    from groq import Groq
                    client = Groq(api_key=gkey)
                    stream = client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0,
                        stream=True,
                    )
                    for chunk in stream:
                        token = chunk.choices[0].delta.content or ""
                        if token:
                            streamed = True
                            yield _sse({"type": "token", "text": token})
                    if streamed:
                        yield _sse({"type": "done"})
                        return
                except Exception as e:
                    if streamed:
                        yield _sse({"type": "done"})
                        return
                    continue
                    
            if config.GEMINI_API_KEY:
                try:
                    import google.generativeai as genai
                    genai.configure(api_key=config.GEMINI_API_KEY)
                    model = genai.GenerativeModel(
                        "gemini-2.0-flash", system_instruction=system_prompt
                    )
                    resp = model.generate_content(user_prompt, stream=True)
                    for chunk in resp:
                        token = getattr(chunk, "text", "") or ""
                        if token:
                            streamed = True
                            yield _sse({"type": "token", "text": token})
                    if streamed:
                        yield _sse({"type": "done"})
                        return
                except Exception as e:
                    logger.error(f"Gemini SQL stream failed: {e}")
                    
            yield _sse({
                "type": "token",
                "text": "⚠️ LLMs failed to formulate natural language response."
            })
            yield _sse({"type": "done"})
            
        return StreamingResponse(sql_chat_stream_generator(), media_type="text/event-stream")

    # 2. Fallback Path: Standard Vector Search (Tiers A/B)
    retrieved = []
    engine = None
    if not is_conversational:
        # Get the shared engine; degrade cleanly if the vector store is offline.
        engine = get_engine(company_id, required=False)
        if engine is not None:
            # Retrieve + rerank (embedding-based dense search)
            try:
                result = await run_in_threadpool(engine.query, query_text, 12, False)
                retrieved = (
                    result.get("sources", []) if isinstance(result, dict) else []
                )
            except Exception as e:
                logger.error(f"Retrieval failed: {e}")
                return StreamingResponse(
                    _single_message_stream(
                        "Sorry — I hit an error searching the knowledge base. "
                        "Check that the embedding API key is valid and the backend is healthy."
                    ),
                    media_type="text/event-stream",
                )

    # 3. Build the SSE sources payload + LLM context from retrieved chunks.
    MAX_CONTEXT_CHARS = 28000
    MAX_SNIPPET_CHARS = 12000
    sources = []
    context_snippets = []
    used_chars = 0
    
    # Deduplicate citations to prevent repeated badges
    seen_cite = set()
    for s in retrieved:
        meta = s.get("metadata") or {}
        content = s.get("text", "")
        filename = (
            meta.get("source_name")
            or os.path.basename(meta.get("source", ""))
            or "Unknown"
        )
        
        # Unique identifier for the source chunk
        cite_key = (filename, meta.get("sheet"), content[:80])
        if cite_key in seen_cite:
            continue
        seen_cite.add(cite_key)

        sources.append(
            {
                "filename": filename,
                "relative_path": f"./backend/knowledge_base/{filename}",
                "exact_snippet_text": content,
                "score": float(s.get("score", 0.0) or 0.0),
            }
        )

    # Deduplicate context lines to prevent repeats in the prompt
    seen_lines = set()
    for idx, s in enumerate(retrieved, 1):
        meta = s.get("metadata") or {}
        content = s.get("text", "")
        filename = (
            meta.get("source_name")
            or os.path.basename(meta.get("source", ""))
            or "Unknown"
        )

        # Remove overlapping line repeats
        deduped_lines = []
        for line in content.split("\n"):
            line_strip = line.strip()
            if line_strip and line_strip in seen_lines:
                continue
            if line_strip:
                seen_lines.add(line_strip)
            deduped_lines.append(line)
        cleaned_content = "\n".join(deduped_lines).strip()
        if not cleaned_content:
            continue

        if used_chars >= MAX_CONTEXT_CHARS:
            continue
        snippet = cleaned_content[:MAX_SNIPPET_CHARS]
        remaining = MAX_CONTEXT_CHARS - used_chars
        if len(snippet) > remaining:
            snippet = snippet[:remaining] + "\n…[truncated]"
        used_chars += len(snippet)
        source_date = (
            meta.get("date")
            or (meta.get("indexed_at", "") or "")[:10]
            or "Unknown Date"
        )
        context_snippets.append(
            f"[Source {idx}: {filename} (Date: {source_date})]\n{snippet}"
        )

    # 4. Build the streaming generator (sources first, then LLM tokens).
    async def chat_stream_generator():
        yield _sse({"type": "sources", "sources": sources})

        context_block = (
            "\n\n---\n\n".join(context_snippets)
            if context_snippets
            else "(No context snippets found in the database.)"
        )

        system_prompt = _persona(company_display) + f"""

CRITICAL INSTRUCTIONS ON SOURCES AND CITATIONS:
- NEVER include inline source citations, file names, dates, or source links in your response text (e.g. do NOT write things like '[Source: file.xlsx]', 'Source: ...', or reference filenames in parentheses).
- The user interface automatically displays clickable file badges at the bottom of your response based on the returned sources list.

CRITICAL INSTRUCTIONS ON COUNTING AND SUMMARIES:
- When a "Table Summary" or "Sample Rows Preview" snippet is in the context above, note that it shows only 5 sample preview rows. Never state a total count or report an exhaustive list based solely on preview rows. For exact totals and counts, state the Total Rows count given in the table summary or rely on database SQL results.
- If a question asks for a count, list, or summation of items (e.g. "How many...", "List all...", "How many good-to-haves..."):
  1. Carefully find all instances of those items across the entire provided context block.
  2. List the items step-by-step mentally or in your reasoning to count them accurately.
  3. Ensure that the total count you state matches the list of items you present.
  4. If the context has fragmented blocks, merge them to avoid contradictory statements (like "we have 4... here are 6").

If the user's query is a simple greeting (e.g., "hi", "hello", "hey", "good morning") or asks about who you are or what you can do, respond politely: explain that you are RippleBot, the technical & configuration assistant for {company_display}, here to answer questions from {company_display}'s documents and meetings.

For specific questions about {company_display}, its documents, or its data:
- If context snippets are provided and contain the answer, answer accurately using ONLY the provided context.
- If the answer is not contained in the context, or if no context snippets are found, respond clearly: "I couldn't find this information in {company_display}'s documents or meeting archives." Do NOT fall back to general product knowledge or anything outside this customer's context.
Do not make up facts outside the context.
Do not use introductory phrases like "Based on the provided context..." or "According to the snippets...". Answer the user's question directly and concisely as a {company_display} technical specialist.
"""

        user_prompt = f"""Context Snippets:
{context_block}

Question: {query_text}

Answer:"""

        streamed = False

        # Priority 1..N: Groq llama-3.3-70b-versatile, one key after another.
        for i, gkey in enumerate(config.GROQ_API_KEYS, 1):
            try:
                from groq import Groq

                client = Groq(api_key=gkey)
                stream = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0,
                    stream=True,
                )
                for chunk in stream:
                    token = chunk.choices[0].delta.content or ""
                    if token:
                        streamed = True
                        yield _sse({"type": "token", "text": token})
                if streamed:
                    yield _sse({"type": "done"})
                    return
                logger.warning(
                    f"Groq key #{i} produced no output. Trying next channel…"
                )
            except Exception as e:
                if streamed:
                    logger.error(f"Groq key #{i} failed mid-stream: {e}")
                    yield _sse({"type": "done"})
                    return
                logger.warning(f"Groq key #{i} failed: {e}. Trying next channel…")
                continue

        # Final fallback: Google Gemini.
        if config.GEMINI_API_KEY:
            try:
                import google.generativeai as genai

                genai.configure(api_key=config.GEMINI_API_KEY)
                model = genai.GenerativeModel(
                    "gemini-2.0-flash", system_instruction=system_prompt
                )
                resp = model.generate_content(user_prompt, stream=True)
                for chunk in resp:
                    token = getattr(chunk, "text", "") or ""
                    if token:
                        streamed = True
                        yield _sse({"type": "token", "text": token})
                if streamed:
                    yield _sse({"type": "done"})
                    return
                logger.warning("Gemini produced no output.")
            except Exception as e:
                logger.error(f"Gemini fallback failed: {e}")

        # Nothing worked.
        yield _sse(
            {
                "type": "token",
                "text": "⚠️ Every LLM channel failed (Groq keys and Gemini). "
                "Check that at least one of GROQ_API_KEY/2/3 or GEMINI_API_KEY is valid in .env.",
            }
        )
        yield _sse({"type": "done"})

    return StreamingResponse(chat_stream_generator(), media_type="text/event-stream")

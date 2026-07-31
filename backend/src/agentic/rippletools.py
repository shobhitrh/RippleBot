"""
RippleBot retrieval tools — the tenant-isolated substrate, exposed as agent tools.

These wrap RippleBot's EXISTING, already-battle-tested functions (no behaviour
change): the Voyage vector engine, the exact-cell index, and the cached uploaded
-Excel SQL store. They are the "owned by RippleBot" half of the no-overlap tool
registry (PRD §18.4). Every function is read-only and scoped to one company_id.

Naming follows the PRD's no-overlap contract:
  * query_uploaded_data  → SQL over CACHED uploaded Excel (NOT live production data)
  * query_live_database  → lives in piatools.py, hits LIVE MySQL

Each tool returns a plain string (what the model reads) plus, where relevant,
appends structured sources onto a caller-supplied list so the SSE layer can emit
the same {file, snippet} citations the existing chat UI already renders.
"""
from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# Keep tool output bounded so a single call can't flood the context window.
_MAX_CHARS = 8000
_MAX_ROWS = 50


def _truncate(text: str) -> str:
    if text and len(text) > _MAX_CHARS:
        return text[:_MAX_CHARS] + "\n[... truncated]"
    return text


def search_knowledge_base(query: str, company_id: str, sources_out: Optional[list] = None) -> str:
    """
    Semantic search over the tenant's uploaded documents (Voyage embed + rerank).
    Wraps engine.query(..., use_llm=False) — retrieval only, no synthesis.
    """
    try:
        from backend.src.rag_engine import get_engine
    except Exception as e:  # pragma: no cover - defensive
        return f"Error: knowledge base engine unavailable ({e})."

    engine = get_engine(company_id, required=False)
    if engine is None:
        return "The knowledge base is temporarily unavailable for this company."

    try:
        result = engine.query(query, 8, use_llm=False)
    except Exception as e:
        logger.error("search_knowledge_base failed: %s", e)
        return f"Error running semantic search: {e}"

    sources = (result or {}).get("sources") or []
    if not sources:
        return "No relevant passages found in the uploaded documents for this query."

    lines: List[str] = []
    for i, s in enumerate(sources, 1):
        text = s.get("text") or s.get("snippet") or s.get("content") or ""
        fname = s.get("source") or s.get("file") or s.get("filename") or "document"
        section = s.get("section") or s.get("sheet") or ""
        header = f"[{i}] {fname}" + (f" — {section}" if section else "")
        lines.append(f"{header}\n{text}".strip())
        if sources_out is not None:
            sources_out.append({"file": fname, "snippet": (text or "")[:280]})

    return _truncate("Relevant passages:\n\n" + "\n\n".join(lines))


def exact_cell_lookup(query: str, company_id: str) -> str:
    """
    Deterministic exact-value lookup against the persisted __cell_index__ (Tier A).
    Returns the matching cell/row context, or a clear miss message.
    """
    try:
        from backend.src import table_store
    except Exception as e:  # pragma: no cover
        return f"Error: table store unavailable ({e})."

    try:
        hit = table_store.cell_lookup(query, company_id)
    except Exception as e:
        logger.error("exact_cell_lookup failed: %s", e)
        return f"Error during cell lookup: {e}"

    if not hit:
        return "No exact cell/value match found for this query."
    return _truncate(f"Exact match:\n{hit}")


def query_uploaded_data(sql: str, company_id: str) -> str:
    """
    Run a read-only SELECT against the tenant's CACHED uploaded-Excel tables
    (Tier B). This is NOT the live production DB — see query_live_database for that.
    The underlying store is read-only by construction; this adds a keyword guard.
    """
    ok, why = _is_safe_select(sql)
    if not ok:
        return f"Refused: {why} Only read-only SELECT/SHOW/DESCRIBE/EXPLAIN/WITH is allowed."

    try:
        from backend.src import table_store
        columns, rows = table_store.execute_select(sql, company_id)
    except Exception as e:
        logger.error("query_uploaded_data failed: %s", e)
        return f"Error executing query on uploaded data: {e}"

    return _format_rows(columns, rows)


def get_document_section(source: str, company_id: str, sheet: Optional[str] = None) -> str:
    """
    Fetch the full chunk set for one document (and optionally one sheet/section)
    — used when the model needs complete context for aggregation/summary.
    """
    try:
        from backend.src.rag_engine import get_engine
    except Exception as e:  # pragma: no cover
        return f"Error: engine unavailable ({e})."

    engine = get_engine(company_id, required=False)
    if engine is None:
        return "The knowledge base is temporarily unavailable for this company."
    try:
        chunks = engine.get_chunks_for(source, sheet)
    except Exception as e:
        logger.error("get_document_section failed: %s", e)
        return f"Error fetching document section: {e}"

    if not chunks:
        return f"No content found for '{source}'" + (f" / '{sheet}'" if sheet else "") + "."
    body = "\n\n".join((c.get("text") or c.get("content") or "") for c in chunks[:20])
    return _truncate(f"Content of {source}" + (f" / {sheet}" if sheet else "") + ":\n\n" + body)


# --------------------------------------------------------------------------- #
# Shared read-only SQL safety (mirrors PIA's _is_safe_select, tools.py:647).   #
# Used here for uploaded-data SQL and re-imported by piatools for live SQL.    #
# --------------------------------------------------------------------------- #
import re as _re

_ALLOWED_STARTS = ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH")
_DANGEROUS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE",
    "GRANT", "REVOKE", "REPLACE", "RENAME", "LOCK", "MERGE", "CALL", "EXEC",
)


def _is_safe_select(sql: str):
    """Return (ok, reason). Read-only guard: allowed start word + no dangerous keywords + single statement."""
    if not sql or not sql.strip():
        return False, "Empty query."
    s = sql.strip().rstrip(";")
    if ";" in s:
        return False, "Multiple statements are not allowed."
    first = s.split(None, 1)[0].upper()
    if first not in _ALLOWED_STARTS:
        return False, f"Query must start with one of {_ALLOWED_STARTS}."
    upper = s.upper()
    for kw in _DANGEROUS:
        if _re.search(r"\b" + kw + r"\b", upper):
            return False, f"Dangerous keyword '{kw}' is not allowed."
    return True, ""


def _format_rows(columns, rows) -> str:
    """Render (columns, rows) as a compact markdown table, capped at _MAX_ROWS."""
    if not columns:
        return "Query returned no columns."
    rows = list(rows or [])
    if not rows:
        return "Query returned 0 rows."
    capped = rows[:_MAX_ROWS]
    head = "| " + " | ".join(str(c) for c in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(str(v)[:60] if v is not None else "" for v in r) + " |"
        for r in capped
    ]
    note = f"\n\n_({len(capped)} of {len(rows)} rows shown — refine the query to narrow.)_" if len(rows) > _MAX_ROWS else ""
    return _truncate("\n".join([head, sep, *body]) + note)

"""
Unified tool registry — the no-overlap contract (PRD §18.4) in code.

TOOL_SCHEMAS are the Anthropic tool-use definitions handed to Claude. execute_tool
is the single dispatch point (mirrors PIA's tools.py:1170). Every call is scoped to
one company_id; RippleBot tools read the tenant's own data, PIA tools reach live /
help-center sources (stubbed until keyed).

The golden rule encoded here: "uploaded" data → RippleBot tools; "live" data → PIA
tools. The tool names carry the boundary so the two SQL engines never collide.
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.src.agentic import rippletools, piatools

logger = logging.getLogger(__name__)

TOOL_SCHEMAS = [
    {
        "name": "search_knowledge_base",
        "description": (
            "Semantic search over THIS company's uploaded documents (PDFs, Excel, "
            "policies, meeting transcripts) using Voyage embeddings + reranking. Use "
            "for questions about company-specific content, policies, or figures that "
            "live in uploaded files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to search for."}},
            "required": ["query"],
        },
    },
    {
        "name": "exact_cell_lookup",
        "description": (
            "Deterministic exact-value lookup from indexed spreadsheet cells. Use when "
            "the user names a specific value, label, or cell that should match exactly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The value/label to find."}},
            "required": ["query"],
        },
    },
    {
        "name": "query_uploaded_data",
        "description": (
            "Run a read-only SELECT against structured tables built from THIS company's "
            "UPLOADED Excel/CSV files (counts, sums, averages, group-by). This is NOT the "
            "live production database — only data the company uploaded, and it is backed by "
            "SQLite, so there is NO information_schema/SHOW TABLES here. To discover live "
            "schemas or tables, use list_live_schemas or query_live_database instead. Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "A single read-only SELECT statement."}},
            "required": ["sql"],
        },
    },
    {
        "name": "get_document_section",
        "description": (
            "Fetch the full content of one uploaded document (optionally one sheet/section) "
            "when you need complete context to summarize or aggregate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "The document filename."},
                "sheet": {"type": "string", "description": "Optional sheet/section name."},
            },
            "required": ["source"],
        },
    },
    {
        "name": "query_live_database",
        "description": (
            "Run a read-only SELECT against the LIVE production database for real-time "
            "operational data that is NEVER uploaded as a file — e.g. current hire counts, "
            "live config values, application status. The live server holds many per-tenant "
            "schemas; omit `schema` to fan out across all of them (results are labelled per "
            "schema), or pass a specific `schema` (see list_live_schemas) to target one. "
            "Use ONLY for live/current data. Supports information_schema for table/column "
            "discovery (e.g. SELECT table_name FROM information_schema.tables WHERE "
            "table_schema='axis_buddyto'). Read-only and audited."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single read-only SELECT statement."},
                "schema": {"type": "string", "description": "Optional: target one schema instead of fanning out."},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "get_tenant_configs",
        "description": (
            "Resolve a named tenant's effective configuration (overrides + platform "
            "defaults) from the live database in one call. Use for 'what is X set to for "
            "company Y' questions. Fans out across tenant schemas to find the company "
            "wherever it lives; pass `schema` to target one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_name": {"type": "string", "description": "The company/tenant name."},
                "search_term": {"type": "string", "description": "Optional config keyword filter."},
                "schema": {"type": "string", "description": "Optional: target one schema instead of fanning out."},
            },
            "required": ["tenant_name"],
        },
    },
    {
        "name": "list_live_schemas",
        "description": (
            "List the live database schemas available for querying (each is a per-tenant "
            "instance with its own config tables). Call this first when you need to target "
            "a specific tenant's schema."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_help_center",
        "description": (
            "Search the product help center for how-to guides, feature explanations, and "
            "setup instructions. Prefer this FIRST for 'how do I…' / 'what does X do' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The how-to / feature question."}},
            "required": ["query"],
        },
    },
]


def execute_tool(name: str, args: dict, company_id: str, sources_out: Optional[list] = None) -> str:
    """Single dispatch point. Returns the string the model reads. Never raises."""
    args = args or {}
    try:
        if name == "search_knowledge_base":
            return rippletools.search_knowledge_base(args.get("query", ""), company_id, sources_out)
        if name == "exact_cell_lookup":
            return rippletools.exact_cell_lookup(args.get("query", ""), company_id)
        if name == "query_uploaded_data":
            return rippletools.query_uploaded_data(args.get("sql", ""), company_id)
        if name == "get_document_section":
            return rippletools.get_document_section(args.get("source", ""), company_id, args.get("sheet"))
        if name == "query_live_database":
            return piatools.query_live_database(args.get("sql", ""), company_id, args.get("schema"))
        if name == "get_tenant_configs":
            return piatools.get_tenant_configs(
                args.get("tenant_name", ""), company_id, args.get("search_term", ""), args.get("schema")
            )
        if name == "list_live_schemas":
            return piatools.list_live_schemas(company_id)
        if name == "search_help_center":
            return piatools.search_help_center(args.get("query", ""), company_id, sources_out)
        return f"Unknown tool: {name}"
    except Exception as e:  # pragma: no cover - dispatch is defensive
        logger.error("execute_tool(%s) failed: %s", name, e)
        return f"Tool '{name}' error: {e}"

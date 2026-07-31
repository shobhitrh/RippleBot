"""
PIA tools — live operational data + help center.

This is the "owned by PIA" half of the no-overlap tool registry (PRD §18.4). These
tools reach data RippleBot never had: LIVE production MySQL (hire counts, config
values, real-time state) and the Document360 help center.

STUB-UNTIL-KEYED CONTRACT (PRD §19.8):
  * If the relevant credentials are absent from .env, each tool returns a clear,
    human-readable "not configured" string naming exactly what to set. It never
    raises and never blocks the loop.
  * The moment the keys are present, the SAME functions execute for real — the
    live-MySQL fan-out and D360 fetch are fully implemented below. Onboarding the
    keys is a config change, not a code change.

Safety (mirrors PIA's tools.py): read-only SELECT-only, whole-word keyword scan,
row cap, and an append-only audit log for every live query. The live DB user MUST
ALSO be read-only at the grant level (PRD §19.2 compliance gate).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from backend.src import config
from backend.src.agentic.rippletools import _is_safe_select, _format_rows

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 1. query_live_database — live production MySQL (read-only, audited)          #
# --------------------------------------------------------------------------- #
def query_live_database(sql: str, company_id: str) -> str:
    """
    Execute a read-only SELECT against the LIVE operational MySQL DB for real-time
    data that is never uploaded as a file (hire counts, live config, current state).
    Scoped to the requesting tenant; every call is appended to the audit log.
    """
    if not config.live_db_configured():
        return (
            "⚙️ Live database is not configured yet. To enable real-time operational "
            "queries, set DB_HOST, DB_USER, DB_PASSWORD and DB_NAME in .env (use a "
            "READ-ONLY MySQL user). Until then, answer from uploaded documents only."
        )

    ok, why = _is_safe_select(sql)
    if not ok:
        _audit(company_id, sql, ok=False, err=why)
        return f"Refused: {why} Only read-only SELECT/SHOW/DESCRIBE/EXPLAIN/WITH is allowed."

    try:
        import pymysql  # lazy — not needed unless live DB is actually used
    except Exception:
        return "⚙️ Live database driver not installed. Add 'pymysql' to requirements and redeploy."

    conn = None
    try:
        conn = pymysql.connect(
            host=config.LIVE_DB_HOST,
            port=config.LIVE_DB_PORT,
            user=config.LIVE_DB_USER,
            password=config.LIVE_DB_PASSWORD or "",
            database=config.LIVE_DB_NAME,
            connect_timeout=8,
            read_timeout=15,
            cursorclass=pymysql.cursors.Cursor,
        )
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(config.LIVE_DB_MAX_ROWS)
        _audit(company_id, sql, ok=True, rows=len(rows))
        return _format_rows(columns, rows)
    except Exception as e:
        logger.error("query_live_database failed: %s", e)
        _audit(company_id, sql, ok=False, err=str(e))
        return f"Error querying live database: {e}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def get_tenant_configs(tenant_name: str, company_id: str, search_term: str = "") -> str:
    """
    Resolve a tenant's effective config (overrides + platform defaults) in one call.
    PIA's JOIN shortcut (company_mstr ⋈ config_mstr ⋈ company_config). Read-only.
    """
    if not config.live_db_configured():
        return (
            "⚙️ Live database is not configured yet — tenant config lookup needs the "
            "live MySQL connection (DB_HOST/DB_USER/DB_PASSWORD/DB_NAME in .env)."
        )
    # The canonical JOIN, parameterised via a WITH/SELECT the safety guard accepts.
    like = f"%{search_term}%" if search_term else "%"
    sql = (
        "SELECT cm.CONFIG_CD, cc.VALUE AS tenant_value, cm.DEFAULT_VALUE, cm.Description "
        "FROM company_mstr comp "
        "JOIN config_mstr cm ON 1=1 "
        "LEFT JOIN company_config cc ON cc.CONFIG_MSTR_SEQ = cm.CONFIG_MSTR_SEQ "
        "AND cc.COMPANY_MSTR_SEQ = comp.COMPANY_MSTR_SEQ "
        f"WHERE comp.COMPANY_NAME LIKE '%{_escape(tenant_name)}%' "
        f"AND (cm.CONFIG_CD LIKE '{_escape(like)}' OR cm.Description LIKE '{_escape(like)}') "
        "LIMIT 200"
    )
    return query_live_database(sql, company_id)


# --------------------------------------------------------------------------- #
# 2. search_help_center — Document360 (semantic once ingested; stub until keyed)#
# --------------------------------------------------------------------------- #
def search_help_center(query: str, company_id: str, sources_out: Optional[list] = None) -> str:
    """
    Search the help center for how-to / feature / setup answers.

    Target design (PRD §16): D360 article bodies ingested into the Voyage store so
    this is semantic, not keyword. Until DOCUMENT360_API_KEY is set AND articles are
    ingested, this returns a clear not-configured message.
    """
    if not config.DOCUMENT360_API_KEY:
        return (
            "⚙️ Help center is not configured yet. Set DOCUMENT360_API_KEY (and run the "
            "D360 ingest so article bodies are embedded into the Voyage store) to enable "
            "semantic how-to answers."
        )
    # Help articles are product-wide: they live in ONE shared store
    # (config.HELP_CENTER_COMPANY_ID), not per tenant. Query that store regardless
    # of which company the user belongs to.
    try:
        from backend.src.agentic.rippletools import search_knowledge_base
        result = search_knowledge_base(query, config.HELP_CENTER_COMPANY_ID, sources_out)
        if "No relevant passages" in result or "temporarily unavailable" in result:
            return (
                "No matching help-center article found. If the help center was just "
                "configured, run the D360 ingest (python -m backend.src.agentic.ingest_d360) "
                "so article bodies are embedded."
            )
        return result
    except Exception as e:
        logger.error("search_help_center failed: %s", e)
        return f"Error searching help center: {e}"


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _escape(v: str) -> str:
    """Minimal single-quote escape for LIKE literals in the read-only path."""
    return (v or "").replace("'", "''").replace("\\", "\\\\")


def _audit(company_id: str, sql: str, ok: bool, rows: Optional[int] = None, err: Optional[str] = None) -> None:
    """Append-only audit log for every live-DB query. Never raises."""
    try:
        ts = datetime.now(timezone.utc).isoformat()
        status = "OK" if ok else "FAIL"
        line = f"{ts}\t{company_id}\t{status}\trows={rows}\terr={err}\t{sql}\n"
        with open(config.DB_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

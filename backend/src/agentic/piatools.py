"""
PIA tools — live operational data (multi-schema fan-out) + help center.

This is the "owned by PIA" half of the no-overlap tool registry (PRD §18.4). These
tools reach data RippleBot never had: LIVE production MySQL and the Document360 help
center.

Live-DB topology (PRD §15.4): the server holds many per-tenant schemas, each with its
own company_mstr/config_mstr/company_config. So the live tools FAN OUT across schemas
and return per-schema-labelled results — PIA's _fanout pattern, adapted from
"many DB servers" to "many schemas on one server". Callers may also target a single
schema explicitly.

STUB-UNTIL-KEYED (PRD §19.8): if creds are absent, tools return a clear "not
configured" string instead of raising. The moment DB_HOST/DB_USER/DB_PASSWORD are set,
the same functions execute for real.

Safety (mirrors PIA): read-only SELECT-only, whole-word keyword scan, row cap, and an
append-only audit log. The live DB user MUST ALSO be read-only at the grant level
(PRD §19.2 compliance gate).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

from backend.src import config
from backend.src.agentic.rippletools import _is_safe_select, _format_rows

logger = logging.getLogger(__name__)

# Cache of auto-discovered config-bearing schemas (TTL, in-process).
_SCHEMA_CACHE = {"ts": 0.0, "schemas": []}
_SCHEMA_TTL = 300.0
# Per-schema tenant-name column: company_mstr uses COMPANY_NAME in the *_buddyto/
# base schemas but DISPLAY_NAME in the *_cp/portal (candidate-portal) schemas.
_NAME_COL_CACHE: dict = {}


# --------------------------------------------------------------------------- #
# Connection + schema discovery                                               #
# --------------------------------------------------------------------------- #
def _connect(schema: Optional[str] = None):
    """Open a read-only-intent pymysql connection, optionally scoped to a schema."""
    import pymysql  # lazy — only imported when the live DB is actually used
    return pymysql.connect(
        host=config.LIVE_DB_HOST,
        port=config.LIVE_DB_PORT,
        user=config.LIVE_DB_USER,
        password=config.LIVE_DB_PASSWORD or "",
        database=schema,
        connect_timeout=8,
        read_timeout=20,
        cursorclass=pymysql.cursors.Cursor,
    )


def _config_schemas() -> list:
    """
    Target schemas for fan-out. Explicit LIVE_DB_SCHEMAS allowlist wins; otherwise
    auto-discover schemas that contain company_mstr (the marker of a tenant-config
    schema) and cache the result. Bounded by LIVE_DB_FANOUT_MAX.
    """
    if config.LIVE_DB_SCHEMAS:
        return config.LIVE_DB_SCHEMAS[: config.LIVE_DB_FANOUT_MAX]
    now = time.monotonic()
    if _SCHEMA_CACHE["schemas"] and (now - _SCHEMA_CACHE["ts"]) < _SCHEMA_TTL:
        return _SCHEMA_CACHE["schemas"]
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT table_schema FROM information_schema.tables "
                "WHERE table_name = 'company_mstr' ORDER BY table_schema"
            )
            schemas = [r[0] for r in cur.fetchall()]
        conn.close()
        schemas = schemas[: config.LIVE_DB_FANOUT_MAX]
        _SCHEMA_CACHE.update(ts=now, schemas=schemas)
        return schemas
    except Exception as e:
        logger.error("live schema discovery failed: %s", e)
        return []


def _run_query(schema: Optional[str], sql: str) -> Tuple[list, list, Optional[str]]:
    """Run one read-only query against one schema. Returns (columns, rows, error)."""
    try:
        conn = _connect(schema)
    except Exception as e:
        return [], [], f"connection failed: {e}"
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(config.LIVE_DB_MAX_ROWS)
        return columns, list(rows), None
    except Exception as e:
        return [], [], str(e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _fanout(sql: str, company_id: str, schemas: list) -> str:
    """Run `sql` across every target schema, returning per-schema-labelled markdown."""
    parts, total = [], 0
    hits = 0
    for sch in schemas:
        cols, rows, err = _run_query(sch, sql)
        if err:
            parts.append(f"### {sch}\n[error: {err}]")
        elif not rows:
            continue  # skip empty schemas to keep the output focused
        else:
            hits += 1
            total += len(rows)
            parts.append(f"### {sch}\n{_format_rows(cols, rows)}")
    _audit(company_id, f"[fanout x{len(schemas)}] {sql}", ok=True, rows=total)
    if not parts:
        return f"No rows found in any of the {len(schemas)} schemas."
    header = f"Results across {hits} of {len(schemas)} schemas:\n\n"
    return header + "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# 1. query_live_database — live production MySQL (read-only, audited, fan-out) #
# --------------------------------------------------------------------------- #
def query_live_database(sql: str, company_id: str, schema: Optional[str] = None) -> str:
    """
    Execute a read-only SELECT against the LIVE operational MySQL for real-time data.
    If `schema` is given, runs against that one schema; otherwise FANS OUT across all
    tenant-config schemas and labels results per schema. Every call is audited.
    """
    if not config.live_db_configured():
        return (
            "⚙️ Live database is not configured yet. Set DB_HOST, DB_USER and "
            "DB_PASSWORD in .env (use a READ-ONLY MySQL user). Until then, answer from "
            "uploaded documents only."
        )
    ok, why = _is_safe_select(sql)
    if not ok:
        _audit(company_id, sql, ok=False, err=why)
        return f"Refused: {why} Only read-only SELECT/SHOW/DESCRIBE/EXPLAIN/WITH is allowed."
    try:
        import pymysql  # noqa: F401 — ensure the driver exists before we try
    except Exception:
        return "⚙️ Live database driver not installed. Add 'pymysql' to requirements and redeploy."

    try:
        if schema:
            cols, rows, err = _run_query(schema, sql)
            if err:
                _audit(company_id, f"[{schema}] {sql}", ok=False, err=err)
                return f"Error querying schema '{schema}': {err}"
            _audit(company_id, f"[{schema}] {sql}", ok=True, rows=len(rows))
            return _format_rows(cols, rows)

        schemas = _config_schemas()
        if not schemas:
            return "No tenant schemas discovered to query. Set LIVE_DB_SCHEMAS or check DB access."
        return _fanout(sql, company_id, schemas)
    except Exception as e:
        logger.error("query_live_database failed: %s", e)
        _audit(company_id, sql, ok=False, err=str(e))
        return f"Error querying live database: {e}"


def _company_name_col(conn, schema: str) -> Optional[str]:
    """
    Return the tenant-name column of `schema`.company_mstr — COMPANY_NAME (base
    schemas) or DISPLAY_NAME (candidate-portal schemas), or None if neither. Cached.
    """
    if schema in _NAME_COL_CACHE:
        return _NAME_COL_CACHE[schema]
    col = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name='company_mstr' "
                "AND column_name IN ('COMPANY_NAME','DISPLAY_NAME')",
                (schema,),
            )
            cols = {r[0] for r in cur.fetchall()}
        col = "COMPANY_NAME" if "COMPANY_NAME" in cols else ("DISPLAY_NAME" if "DISPLAY_NAME" in cols else None)
    except Exception:
        col = None
    _NAME_COL_CACHE[schema] = col
    return col


def get_tenant_configs(
    tenant_name: str, company_id: str, search_term: str = "", schema: Optional[str] = None
) -> str:
    """
    Resolve a tenant's effective config (overrides + platform defaults) — PIA's JOIN
    shortcut. Without `schema`, fans out across all tenant-config schemas to find the
    tenant wherever it lives. Detects the correct tenant-name column per schema
    (COMPANY_NAME vs DISPLAY_NAME) so it works across the differing schema shapes.
    Read-only.
    """
    if not config.live_db_configured():
        return (
            "⚙️ Live database is not configured yet — tenant config lookup needs the "
            "live MySQL connection (DB_HOST/DB_USER/DB_PASSWORD in .env)."
        )
    schemas = [schema] if schema else _config_schemas()
    if not schemas:
        return "No tenant schemas discovered to query."

    like = f"%{search_term}%" if search_term else "%"
    t, lk = _escape(tenant_name), _escape(like)
    parts: list = []
    hits = 0
    for sch in schemas:
        try:
            conn = _connect(sch)
        except Exception as e:
            parts.append(f"### {sch}\n[connection failed: {e}]")
            continue
        try:
            name_col = _company_name_col(conn, sch)
            if not name_col:
                continue  # this schema's company_mstr has no recognizable name column
            # name_col is from a fixed allowlist; tenant/search are escaped → read-only SELECT.
            sql = (
                f"SELECT DISTINCT comp.{name_col} AS company, cm.CONFIG_CD, "
                "cc.VALUE AS tenant_value, cm.DEFAULT_VALUE, cm.Description "
                "FROM company_mstr comp JOIN config_mstr cm ON 1=1 "
                "LEFT JOIN company_config cc ON cc.CONFIG_MSTR_SEQ = cm.CONFIG_MSTR_SEQ "
                "AND cc.COMPANY_MSTR_SEQ = comp.COMPANY_MSTR_SEQ "
                f"WHERE comp.{name_col} LIKE '%{t}%' "
                f"AND (cm.CONFIG_CD LIKE '{lk}' OR cm.Description LIKE '{lk}') "
                f"ORDER BY comp.{name_col}, cm.CONFIG_CD LIMIT 200"
            )
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in (cur.description or [])]
                rows = cur.fetchmany(config.LIVE_DB_MAX_ROWS)
            if rows:
                hits += 1
                parts.append(f"### {sch}\n{_format_rows(cols, rows)}")
        except Exception as e:
            parts.append(f"### {sch}\n[error: {e}]")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    _audit(company_id, f"[get_tenant_configs {tenant_name}] x{len(schemas)}", ok=True, rows=hits)
    if not parts:
        return f"No config found for '{tenant_name}' in any of the {len(schemas)} schemas."
    return f"Config for '{tenant_name}' across {hits} of {len(schemas)} schemas:\n\n" + "\n\n".join(parts)


def list_live_schemas(company_id: Optional[str] = None) -> str:
    """List the live schemas available for fan-out (each has the tenant-config tables)."""
    if not config.live_db_configured():
        return "⚙️ Live database is not configured yet (set DB_HOST/DB_USER/DB_PASSWORD)."
    schemas = _config_schemas()
    if not schemas:
        return "No live schemas discovered."
    return (
        "Live schemas available (each has company_mstr/config_mstr/company_config). "
        "Pass one as `schema` to target it, or omit to fan out across all:\n"
        + "\n".join(f"- {s}" for s in schemas)
    )


# --------------------------------------------------------------------------- #
# 2. search_help_center — Document360 (semantic; shared store)                #
# --------------------------------------------------------------------------- #
def search_help_center(query: str, company_id: str, sources_out: Optional[list] = None) -> str:
    """
    Search the help center for how-to / feature / setup answers. Help articles live in
    ONE shared store (config.HELP_CENTER_COMPANY_ID), ingested via ingest_d360.
    """
    if not config.DOCUMENT360_API_KEY:
        return (
            "⚙️ Help center is not configured yet. Set DOCUMENT360_API_KEY (and run the "
            "D360 ingest so article bodies are embedded into the Voyage store) to enable "
            "semantic how-to answers."
        )
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
    return (v or "").replace("\\", "\\\\").replace("'", "''")


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

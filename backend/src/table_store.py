"""
Tier-C structured-table store for the chat SQL router.

Excel/CSV sheets are loaded into real relational tables so the router can answer
quantitative questions (counts, sums, exact lookups) precisely. There are two
backends, chosen by ``config.VECTOR_BACKEND`` so the tables live wherever the
rest of the tenant's data lives:

  * ``pgvector`` → tables live in the SAME managed Postgres as the vectors, in a
    per-tenant schema ``tenant_<company_id>``. Nothing on local disk → the web
    container is stateless.
  * anything else (local dev ``chroma``) → per-tenant SQLite file on disk
    (``<DOCUMENTS_DIR>/db/<company_id>_tables.db``) — unchanged legacy behaviour.

Backend-agnostic API used by the ingest pipeline and the chat router:

  load_tables(tables, company_id)
  delete_tables_for_file(filename, company_id)
  get_router_schema(company_id)  -> {physical_name: {title, source_key, columns:[{name,type,samples}]}}
  execute_select(sql, company_id) -> (column_names, rows)

The SQLite path is byte-for-byte the same logic the router used before; the
Postgres path mirrors it (typed columns so SUM/AVG work, per-tenant schema for
isolation, read-only execution for safety).
"""

import os
import re
import json
import sqlite3
import hashlib
import logging
from typing import List, Dict, Tuple, Optional

import pandas as pd

from backend.src import config
from backend.src.excel_parser import get_db_path, sanitize_name

logger = logging.getLogger(__name__)

_PG_ENGINE = None  # cached SQLAlchemy engine (Postgres backend only)

META_TABLE = "__table_metadata__"


def use_postgres() -> bool:
    """Postgres Tier-C when the vector backend is pgvector; else SQLite."""
    return config.VECTOR_BACKEND == "pgvector"


# ── shared dataframe prep ────────────────────────────────────────────────────
def _prep_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Stringify datetimes (parallel to the legacy SQLite loader so both backends
    behave identically) and clamp column names to Postgres's 63-char identifier
    limit, keeping them unique.
    """
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            df[col] = df[col].apply(
                lambda x: x.isoformat() if hasattr(x, "isoformat") else x
            )

    new_cols, seen = [], {}
    for c in df.columns:
        cc = (str(c) or "col")[:63]
        if cc in seen:
            seen[cc] += 1
            suffix = f"_{seen[cc]}"
            cc = cc[: 63 - len(suffix)] + suffix
        else:
            seen[cc] = 1
        new_cols.append(cc)
    df.columns = new_cols
    return df


# ══════════════════════════════════════════════════════════════════════════════
# Postgres backend
# ══════════════════════════════════════════════════════════════════════════════
def _pg_engine():
    global _PG_ENGINE
    if _PG_ENGINE is None:
        from sqlalchemy import create_engine

        _PG_ENGINE = create_engine(config.POSTGRES_URI, pool_pre_ping=True)
    return _PG_ENGINE


def _pg_conn():
    """Raw psycopg2 connection (autocommit) for DDL/introspection."""
    import psycopg2

    c = psycopg2.connect(config.POSTGRES_URI)
    c.autocommit = True
    return c


def _tenant_schema(company_id: Optional[str]) -> str:
    cid = config.normalize_company_id(company_id or config.DEFAULT_COMPANY_ID)
    safe = re.sub(r"[^a-z0-9_]", "_", cid.lower())[:50].strip("_") or "default"
    return f"tenant_{safe}"


def _physical_name(logical: str) -> str:
    """
    Deterministic ≤63-char physical table name for a logical name (which may be
    long). Deterministic so re-ingest replaces the same table; hash suffix keeps
    it unique even after truncation.
    """
    base = sanitize_name(logical)
    h = hashlib.md5(logical.encode("utf-8")).hexdigest()[:8]
    return f"{base[:54]}_{h}"


def _qi(ident: str) -> str:
    """Quote a Postgres identifier safely (identifiers are validated/sanitized
    upstream, but double-quote + escape to be safe)."""
    return '"' + ident.replace('"', '""') + '"'


def _pg_load(tables: List[Tuple[str, pd.DataFrame, str]], company_id: Optional[str]):
    schema = _tenant_schema(company_id)
    conn = _pg_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_qi(schema)};")
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS {_qi(schema)}.{_qi(META_TABLE)} "
            f"(table_name TEXT PRIMARY KEY, title TEXT, source_key TEXT);"
        )
        engine = _pg_engine()
        for logical, df, title in tables:
            if df is None or df.empty:
                continue
            phys = _physical_name(logical)
            prepped = _prep_df(df)
            # pandas infers Postgres column types from dtypes: numeric columns →
            # DOUBLE/BIGINT (so SUM/AVG work), everything else → TEXT.
            prepped.to_sql(
                name=phys,
                con=engine,
                schema=schema,
                if_exists="replace",
                index=False,
            )
            cur.execute(
                f"INSERT INTO {_qi(schema)}.{_qi(META_TABLE)} (table_name, title, source_key) "
                f"VALUES (%s, %s, %s) "
                f"ON CONFLICT (table_name) DO UPDATE SET title = EXCLUDED.title, source_key = EXCLUDED.source_key;",
                (phys, title, logical),
            )
            # Build tsvector column for full-text search across all text columns
            try:
                text_cols = [c for c in prepped.columns if not pd.api.types.is_numeric_dtype(prepped[c])]
                if text_cols:
                    concat_expr = " || ' ' || ".join(f"coalesce({_qi(c)}, '')" for c in text_cols)
                    cur.execute(
                        f"ALTER TABLE {_qi(schema)}.{_qi(phys)} ADD COLUMN IF NOT EXISTS _fts_vector tsvector;"
                    )
                    cur.execute(
                        f"UPDATE {_qi(schema)}.{_qi(phys)} "
                        f"SET _fts_vector = to_tsvector('english', {concat_expr});"
                    )
                    cur.execute(
                        f"CREATE INDEX IF NOT EXISTS {_qi('fts_idx_' + sanitize_name(phys))} "
                        f"ON {_qi(schema)}.{_qi(phys)} USING GIN(_fts_vector);"
                    )
            except Exception as fts_err:
                logger.debug(f"FTS index creation skipped for {phys}: {fts_err}")

            logger.info(f"[pg] loaded table {schema}.{phys} ({len(prepped)} rows)")
    except Exception as e:
        logger.error(f"[pg] load_tables failed: {e}", exc_info=True)
    finally:
        conn.close()


def _pg_delete_for_file(filename: str, company_id: Optional[str]):
    schema = _tenant_schema(company_id)
    prefix = sanitize_name(filename)
    conn = _pg_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT table_name FROM {_qi(schema)}.{_qi(META_TABLE)} WHERE source_key LIKE %s;",
            (prefix + "%",),
        )
        rows = cur.fetchall()
        for (phys,) in rows:
            cur.execute(f"DROP TABLE IF EXISTS {_qi(schema)}.{_qi(phys)};")
            cur.execute(
                f"DELETE FROM {_qi(schema)}.{_qi(META_TABLE)} WHERE table_name = %s;",
                (phys,),
            )
            logger.info(f"[pg] dropped table {schema}.{phys}")
    except Exception as e:
        # Missing schema/metadata table just means nothing to delete.
        logger.debug(f"[pg] delete_tables_for_file: {e}")
    finally:
        conn.close()


def _pg_router_schema(company_id: Optional[str]) -> Dict[str, dict]:
    schema = _tenant_schema(company_id)
    out: Dict[str, dict] = {}
    conn = _pg_conn()
    try:
        cur = conn.cursor()
        # Titles + source mapping.
        titles, sources = {}, {}
        try:
            cur.execute(
                f"SELECT table_name, title, source_key FROM {_qi(schema)}.{_qi(META_TABLE)};"
            )
            for name, title, src in cur.fetchall():
                titles[name] = title
                sources[name] = src
        except Exception:
            return {}  # no schema/metadata yet → no tables

        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s;",
            (schema,),
        )
        # Exclude internal bookkeeping tables (__table_metadata__, __cell_index__)
        # so the LLM router can ONLY ever query real data tables — never the
        # plumbing. Querying the index was what produced answers like REQ-020 →
        # "Value Length: 7, Table Name, Source Key".
        table_names = [r[0] for r in cur.fetchall() if not r[0].startswith("__")]

        for t in table_names:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position;",
                (schema, t),
            )
            cols = []
            for col_name, col_type in cur.fetchall():
                samples = []
                is_text = any(
                    t_kw in str(col_type).lower()
                    for t_kw in ("text", "char", "varchar", "string")
                )
                if is_text:
                    try:
                        cur.execute(
                            f"SELECT DISTINCT {_qi(col_name)} FROM {_qi(schema)}.{_qi(t)} "
                            f"WHERE {_qi(col_name)} IS NOT NULL LIMIT 8;"
                        )
                        for (v,) in cur.fetchall():
                            s = str(v).strip()
                            if s and len(s) < 60:
                                samples.append(s)
                    except Exception:
                        pass
                cols.append(
                    {"name": col_name, "type": col_type, "samples": samples[:8]}
                )
            out[t] = {
                "title": titles.get(t),
                "source_key": sources.get(t),
                "columns": cols,
            }
    finally:
        conn.close()
    return out


def _pg_execute_select(sql: str, company_id: Optional[str]) -> Tuple[List[str], list]:
    schema = _tenant_schema(company_id)
    import psycopg2

    conn = psycopg2.connect(config.POSTGRES_URI)
    try:
        cur = conn.cursor()
        # Resolve bare table names to the tenant schema, and forbid writes.
        cur.execute(f"SET search_path TO {_qi(schema)};")
        cur.execute("SET TRANSACTION READ ONLY;")
        cur.execute(sql)
        col_names = [d[0] for d in cur.description]
        rows = cur.fetchall()
        conn.rollback()  # read-only; nothing to commit
        return col_names, rows
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# SQLite backend (legacy — unchanged behaviour)
# ══════════════════════════════════════════════════════════════════════════════
def _sqlite_load(
    tables: List[Tuple[str, pd.DataFrame, str]], company_id: Optional[str]
):
    db_path = get_db_path(company_id)
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {META_TABLE} (table_name TEXT PRIMARY KEY, title TEXT)"
        )
        for table_name, df, title in tables:
            if df is None or df.empty:
                continue
            prepped = _prep_df(df)
            prepped.to_sql(name=table_name, con=conn, if_exists="replace", index=False)
            conn.execute(
                f"INSERT OR REPLACE INTO {META_TABLE} (table_name, title) VALUES (?, ?)",
                (table_name, title),
            )
            logger.info(f"[sqlite] loaded table {table_name} ({len(prepped)} rows)")
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[sqlite] load_tables failed: {e}")


def _sqlite_delete_for_file(filename: str, company_id: Optional[str]):
    db_path = get_db_path(company_id)
    if not os.path.exists(db_path):
        return
    prefix = sanitize_name(filename)
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS {META_TABLE} (table_name TEXT PRIMARY KEY, title TEXT)"
        )
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        for t in [r[0] for r in cur.fetchall()]:
            if t.startswith(prefix) and t != META_TABLE:
                cur.execute(f'DROP TABLE "{t}"')
                cur.execute(f"DELETE FROM {META_TABLE} WHERE table_name = ?", (t,))
                logger.info(f"[sqlite] dropped table {t}")
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[sqlite] delete_tables_for_file failed: {e}")


def _sqlite_router_schema(company_id: Optional[str]) -> Dict[str, dict]:
    db_path = get_db_path(company_id)
    if not os.path.exists(db_path):
        return {}
    out: Dict[str, dict] = {}
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [
            r[0]
            for r in cur.fetchall()
            if not r[0].startswith("sqlite_") and not r[0].startswith("__")
        ]
        if not tables:
            conn.close()
            return {}
        titles = {}
        try:
            cur.execute(f"SELECT table_name, title FROM {META_TABLE}")
            titles = dict(cur.fetchall())
        except Exception:
            pass
        for t in tables:
            cur.execute(f'PRAGMA table_info("{t}")')
            cols = []
            for c in cur.fetchall():
                col_name, col_type = c[1], c[2]
                samples = []
                # Only fetch samples for text/string columns
                is_text = (
                    any(
                        t_kw in str(col_type).lower()
                        for t_kw in ("text", "char", "varchar", "string")
                    )
                    or col_type == ""
                )
                if is_text:
                    try:
                        cur.execute(
                            f'SELECT DISTINCT "{col_name}" FROM "{t}" WHERE "{col_name}" IS NOT NULL LIMIT 8'
                        )
                        for (v,) in cur.fetchall():
                            s = str(v).strip()
                            if s and len(s) < 60:
                                samples.append(s)
                    except Exception:
                        pass
                cols.append(
                    {"name": col_name, "type": col_type, "samples": samples[:8]}
                )
            out[t] = {"title": titles.get(t), "source_key": t, "columns": cols}
        conn.close()
    except Exception as e:
        logger.error(f"[sqlite] router_schema failed: {e}")
        return {}
    return out


def _sqlite_execute_select(
    sql: str, company_id: Optional[str]
) -> Tuple[List[str], list]:
    db_path = get_db_path(company_id)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        col_names = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return col_names, rows
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Public backend-agnostic API
# ══════════════════════════════════════════════════════════════════════════════
def load_tables(tables: List[Tuple[str, pd.DataFrame, str]], company_id: str = None):
    if use_postgres():
        _pg_load(tables, company_id)
    else:
        _sqlite_load(tables, company_id)
    # Persist the exact-value cell index (survives restarts, per-tenant). This
    # replaces the old in-memory GLOBAL_CELL_INDEX, which was empty after every
    # process restart — so the exact-value fast-path never fired in production.
    _build_cell_index(tables, company_id)


def delete_tables_for_file(filename: str, company_id: str = None):
    if use_postgres():
        _pg_delete_for_file(filename, company_id)
    else:
        _sqlite_delete_for_file(filename, company_id)
    _drop_cell_index_for_file(filename, company_id)


# ── Persisted per-tenant exact-value cell index ───────────────────────────────
CELL_INDEX = "__cell_index__"


def _iter_index_values(df: "pd.DataFrame"):
    """
    Yield (column_name, value) for short text & numeric values worth indexing for exact
    lookup — IDs, codes, names, cities, categorical/designation values. Skips
    long free-text (handled by vector search).
    """
    for col in df.columns:
        seen = set()
        for v in df[col].dropna().tolist():
            s = str(v).strip()
            # Strip ".0" suffix from whole-number floats (e.g. 60593501.0 -> "60593501")
            if s.endswith(".0") and s[:-2].isdigit():
                s = s[:-2]
            if 2 <= len(s) <= 60:
                key = s.lower()
                if key not in seen:
                    seen.add(key)
                    yield str(col), s
            if len(seen) >= 5000:
                break


def _build_cell_index(
    tables: List[Tuple[str, pd.DataFrame, str]], company_id: Optional[str]
):
    try:
        if use_postgres():
            schema = _tenant_schema(company_id)
            conn = _pg_conn()
            try:
                from psycopg2.extras import execute_values

                cur = conn.cursor()
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_qi(schema)};")
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {_qi(schema)}.{_qi(CELL_INDEX)} "
                    f"(value_norm TEXT, table_name TEXT, column_name TEXT, val_len INT, source_key TEXT);"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS cellidx_value ON {_qi(schema)}.{_qi(CELL_INDEX)} (value_norm);"
                )
                for logical, df, _title in tables:
                    if df is None or df.empty:
                        continue
                    phys = _physical_name(logical)
                    cur.execute(
                        f"DELETE FROM {_qi(schema)}.{_qi(CELL_INDEX)} WHERE table_name = %s;",
                        (phys,),
                    )
                    rows = [
                        (v.lower(), phys, col, len(v), logical)
                        for col, v in _iter_index_values(df)
                    ]
                    if rows:
                        execute_values(
                            cur,
                            f"INSERT INTO {_qi(schema)}.{_qi(CELL_INDEX)} "
                            f"(value_norm, table_name, column_name, val_len, source_key) VALUES %s",
                            rows,
                        )
                logger.info(
                    f"[pg] cell index built for {len(tables)} table(s), tenant '{company_id}'"
                )
            finally:
                conn.close()
        else:
            db_path = get_db_path(company_id)
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {CELL_INDEX} "
                    f"(value_norm TEXT, table_name TEXT, column_name TEXT, val_len INT, source_key TEXT);"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS cellidx_value ON {CELL_INDEX}(value_norm);"
                )
                for logical, df, _title in tables:
                    if df is None or df.empty:
                        continue
                    cur.execute(
                        f"DELETE FROM {CELL_INDEX} WHERE table_name = ?", (logical,)
                    )
                    rows = [
                        (v.lower(), logical, col, len(v), logical)
                        for col, v in _iter_index_values(df)
                    ]
                    if rows:
                        cur.executemany(
                            f"INSERT INTO {CELL_INDEX} (value_norm, table_name, column_name, val_len, source_key) "
                            f"VALUES (?, ?, ?, ?, ?)",
                            rows,
                        )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        logger.error(f"_build_cell_index failed: {e}")


def _drop_cell_index_for_file(filename: str, company_id: Optional[str]):
    prefix = sanitize_name(filename)
    try:
        if use_postgres():
            schema = _tenant_schema(company_id)
            conn = _pg_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    f"DELETE FROM {_qi(schema)}.{_qi(CELL_INDEX)} WHERE source_key LIKE %s;",
                    (prefix + "%",),
                )
            finally:
                conn.close()
        else:
            db_path = get_db_path(company_id)
            if not os.path.exists(db_path):
                return
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {CELL_INDEX} "
                    f"(value_norm TEXT, table_name TEXT, column_name TEXT, val_len INT, source_key TEXT);"
                )
                cur.execute(
                    f"DELETE FROM {CELL_INDEX} WHERE source_key LIKE ?", (prefix + "%",)
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        logger.debug(f"_drop_cell_index_for_file: {e}")


def cell_lookup(question: str, company_id: str = None) -> Optional[dict]:
    """
    Exact-value routing: find the most specific indexed cell value that appears in
    the question, and return {table, column, value} for it — so a bare value like
    'Head-Wholesale Credit-CB' routes straight to the table+column that contains it
    without depending on the LLM or embeddings. Persisted → works after restarts.

    Handles legacy ".0" suffix: old cell index rows stored "60593501.0" but users
    type "60593501". We match BOTH the raw value_norm AND a ".0"-stripped variant
    so lookups work regardless of when the cell index was built.
    """
    q = (question or "").lower()
    if len(q) < 3:
        return None
    try:
        if use_postgres():
            schema = _tenant_schema(company_id)
            conn = _pg_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT table_name, column_name, value_norm, "
                    f"CASE WHEN value_norm LIKE '%.0' AND length(value_norm) > 2 "
                    f"  AND value_norm ~ '^[0-9]+\\.0$' "
                    f"  THEN length(value_norm) - 2 ELSE length(value_norm) END AS effective_len "
                    f"FROM {_qi(schema)}.{_qi(CELL_INDEX)} "
                    f"WHERE strpos(%s, value_norm) > 0 "
                    f"   OR (value_norm LIKE '%.0' AND strpos(%s, regexp_replace(value_norm, '\\.0$', '')) > 0) "
                    f"ORDER BY effective_len DESC LIMIT 1;",
                    (q, q),
                )
                row = cur.fetchone()
            finally:
                conn.close()
        else:
            db_path = get_db_path(company_id)
            if not os.path.exists(db_path):
                return None
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT table_name, column_name, value_norm, "
                    f"CASE WHEN value_norm LIKE '%.0' AND length(value_norm) > 2 "
                    f"  THEN length(value_norm) - 2 ELSE length(value_norm) END AS effective_len "
                    f"FROM {CELL_INDEX} "
                    f"WHERE instr(?, value_norm) > 0 "
                    f"   OR (value_norm LIKE '%.0' AND instr(?, replace(value_norm, '.0', '')) > 0) "
                    f"ORDER BY effective_len DESC LIMIT 1;",
                    (q, q),
                )
                row = cur.fetchone()
            finally:
                conn.close()
        if row:
            val = str(row[2]).strip()
            if val.endswith(".0") and val[:-2].isdigit():
                val = val[:-2]
            return {"table": row[0], "column": row[1], "value": val}
    except Exception as e:
        logger.debug(f"cell_lookup failed: {e}")
    return None


def plainto_tsquery_sql(query: str) -> str:
    """Build a safe plainto_tsquery SQL fragment."""
    safe = query.replace("'", "''")
    return f"plainto_tsquery('english', '{safe}')"


def fts_search(query: str, company_id: str = None, limit: int = 10) -> List[dict]:
    """
    Full-text search across ALL Tier-C tables for a query using PostgreSQL tsvector.
    Returns list of {table, row_data, rank} dicts.
    """
    if not use_postgres():
        return []

    schema = _tenant_schema(company_id)
    conn = _pg_conn()
    results = []
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = %s AND column_name = '_fts_vector';",
            (schema,),
        )
        tables = [r[0] for r in cur.fetchall()]
        tsquery = plainto_tsquery_sql(query)

        for tbl in tables:
            try:
                cur.execute(
                    f"SELECT *, ts_rank(_fts_vector, {tsquery}) AS _rank "
                    f"FROM {_qi(schema)}.{_qi(tbl)} "
                    f"WHERE _fts_vector @@ {tsquery} "
                    f"ORDER BY _rank DESC LIMIT %s;",
                    (limit,),
                )
                cols = [d[0] for d in cur.description if d[0] != "_fts_vector"]
                for row in cur.fetchall():
                    row_dict = {c: str(v)[:100] for c, v in zip(cols, row[:-1])}
                    results.append({
                        "table": tbl,
                        "row_data": row_dict,
                        "rank": row[-1],
                    })
            except Exception:
                continue

        results.sort(key=lambda x: x["rank"], reverse=True)
    except Exception as e:
        logger.error(f"FTS search failed: {e}")
    finally:
        conn.close()

    return results[:limit]


def fts_cell_lookup(question: str, company_id: str = None) -> Optional[dict]:
    """
    FTS-based fallback: search across ALL tables using PostgreSQL full-text search.
    Returns {route, formatted_result, sources} if found, None otherwise.
    """
    results = fts_search(question, company_id, limit=5)
    if not results:
        return None

    best = results[0]
    col_names = list(best["row_data"].keys())
    header = "| " + " | ".join(col_names) + " |"
    sep = "| " + " | ".join(["---"] * len(col_names)) + " |"
    rows_md = []
    for r in results:
        row = "| " + " | ".join(str(r["row_data"].get(c, "")) for c in col_names) + " |"
        rows_md.append(row)

    return {
        "route": "FTS",
        "results_markdown": f"Full-Text Search Results:\n{header}\n{sep}\n" + "\n".join(rows_md),
        "sources": [],
    }


def get_router_schema(company_id: str = None) -> Dict[str, dict]:
    return (
        _pg_router_schema(company_id)
        if use_postgres()
        else _sqlite_router_schema(company_id)
    )


def execute_select(sql: str, company_id: str = None) -> Tuple[List[str], list]:
    return (
        _pg_execute_select(sql, company_id)
        if use_postgres()
        else _sqlite_execute_select(sql, company_id)
    )


# ── Analytical Engine Interface & Inverted Cell Index ──
class AnalyticalEngine:
    """Database-Agnostic Analytical Engine interface."""

    def load_tables(
        self, tables: List[Tuple[str, pd.DataFrame, str]], company_id: str = None
    ):
        raise NotImplementedError

    def delete_tables_for_file(self, filename: str, company_id: str = None):
        raise NotImplementedError

    def get_router_schema(self, company_id: str = None) -> Dict[str, dict]:
        raise NotImplementedError

    def execute_select(
        self, sql: str, company_id: str = None
    ) -> Tuple[List[str], list]:
        raise NotImplementedError


class InvertedCellIndex:
    """
    Inverted Index for exact cell values (IDs, codes, emails, names).
    Maps tokens -> List of (db_table_name, col_name, row_idx, cell_value)
    and stores full row dicts to format complete Markdown table hits.
    """

    def __init__(self):
        self._index: Dict[str, List[Tuple[str, str, int, str]]] = {}
        self._rows: Dict[Tuple[str, int], Dict[str, str]] = {}
        self._table_columns: Dict[str, List[str]] = {}

    def build_index(self, df: pd.DataFrame, db_table_name: str):
        columns = [str(c) for c in df.columns]
        self._table_columns[db_table_name] = columns

        for row_idx, row in df.iterrows():
            row_dict = {}
            for col_name, val in row.items():
                val_str = "" if pd.isna(val) else str(val).strip()
                row_dict[str(col_name)] = val_str
                if not val_str or len(val_str) < 2:
                    continue

                # Index exact value and tokens
                raw_tokens = set(re.split(r"[^\w]+", val_str.lower()))
                tokens = {t for t in raw_tokens if t}
                tokens.add(val_str.lower())

                for t in tokens:
                    if len(t) >= 2:
                        if t not in self._index:
                            self._index[t] = []
                        if len(self._index[t]) < 100:
                            self._index[t].append(
                                (db_table_name, str(col_name), int(row_idx), val_str)
                            )

            self._rows[(db_table_name, int(row_idx))] = row_dict

    def search(self, query: str) -> List[Tuple[str, str, int, str]]:
        stopwords = {
            "what",
            "can",
            "you",
            "tell",
            "me",
            "about",
            "job",
            "id",
            "code",
            "name",
            "the",
            "a",
            "an",
            "is",
            "are",
            "of",
            "in",
            "for",
            "to",
            "with",
            "on",
            "at",
            "by",
            "from",
            "show",
            "get",
            "find",
            "list",
            "give",
            "details",
            "info",
            "information",
            "value",
            "entry",
            "record",
            "data",
            "sheet",
            "table",
        }
        raw_tokens = set(re.split(r"[^\w]+", query.lower()))
        all_tokens = {t for t in raw_tokens if t}
        all_tokens.add(query.strip().lower())

        # Separate high-specificity tokens (numbers, codes, non-stopwords) from stopwords
        high_spec_tokens = {t for t in all_tokens if t not in stopwords and len(t) >= 2}
        low_spec_tokens = all_tokens - high_spec_tokens

        # Search high-specificity tokens first
        results = []
        seen = set()
        for t in list(high_spec_tokens) + list(low_spec_tokens):
            if t in self._index:
                for item in self._index[t]:
                    if item not in seen:
                        seen.add(item)
                        results.append(item)
        return results

    def search_markdown(self, query: str, limit: int = 5) -> str:
        """
        Search tokens with specificity-based scoring and return formatted Markdown
        snippets of top-matching full rows.
        Injects metadata headers: Table Name, Row Index, Columns.
        """
        raw_hits = self.search(query)
        if not raw_hits:
            return ""

        stopwords = {
            "what",
            "can",
            "you",
            "tell",
            "me",
            "about",
            "job",
            "id",
            "code",
            "name",
            "the",
            "a",
            "an",
            "is",
            "are",
            "of",
            "in",
            "for",
            "to",
            "with",
            "on",
            "at",
            "by",
            "from",
            "show",
            "get",
            "find",
            "list",
            "give",
            "details",
            "info",
            "information",
            "value",
            "entry",
            "record",
            "data",
            "sheet",
            "table",
        }
        query_words = set(re.split(r"[^\w]+", query.lower()))
        specific_words = {w for w in query_words if w not in stopwords and len(w) >= 2}

        # Score matching rows based on token specificity & exact string matches
        row_scores: Dict[Tuple[str, int], float] = {}
        row_data_map: Dict[Tuple[str, int], Dict[str, str]] = {}

        for db_table_name, col_name, row_idx, cell_val in raw_hits:
            row_key = (db_table_name, row_idx)
            row_dict = self._rows.get(row_key)
            if not row_dict:
                continue

            row_data_map[row_key] = row_dict
            cell_lower = cell_val.lower()

            # Calculate row score
            score = 0.0
            for w in specific_words:
                if w in cell_lower:
                    # Digit / numeric match gets highest score
                    if w.isdigit():
                        score += 10.0
                    else:
                        score += 5.0
                if cell_lower == w:
                    score += 10.0

            # Small boost for stopword matches if no specific score
            if score == 0.0:
                score += 0.01

            row_scores[row_key] = max(row_scores.get(row_key, 0.0), score)

        # Sort row keys by score descending
        sorted_row_keys = sorted(
            row_scores.keys(), key=lambda k: row_scores[k], reverse=True
        )

        # Filter out rows with negligible score if high-scoring rows exist
        max_score = row_scores[sorted_row_keys[0]] if sorted_row_keys else 0.0
        if max_score >= 5.0:
            sorted_row_keys = [k for k in sorted_row_keys if row_scores[k] >= 1.0]

        if not sorted_row_keys:
            return ""

        # Group top-scored rows by table name
        table_rows: Dict[str, List[Tuple[int, Dict[str, str]]]] = {}
        for db_table_name, row_idx in sorted_row_keys[:limit]:
            row_dict = row_data_map[(db_table_name, row_idx)]
            if db_table_name not in table_rows:
                table_rows[db_table_name] = []
            table_rows[db_table_name].append((row_idx, row_dict))

        markdown_blocks = []
        for db_table_name, rows_list in table_rows.items():
            cols = self._table_columns.get(db_table_name, list(rows_list[0][1].keys()))

            # Format as Markdown table
            hdr_line = "| " + " | ".join(cols) + " |"
            sep_line = "| " + " | ".join(["---"] * len(cols)) + " |"
            data_lines = []
            for r_idx, r_dict in rows_list:
                vals = [r_dict.get(c, "").replace("\n", " ") for c in cols]
                data_lines.append("| " + " | ".join(vals) + " |")

            block = (
                f"### Exact Cell Index Hit (Table: `{db_table_name}`, Matched {len(rows_list)} Row(s)):\n"
                f"{hdr_line}\n{sep_line}\n" + "\n".join(data_lines)
            )
            markdown_blocks.append(block)

        return "\n\n".join(markdown_blocks)

    def search_markdown_entities(self, entities: List[str], max_hits: int = 10) -> str:
        """
        USIE v4 Entity Search:
        Searches extracted entities against the inverted cell index with a strict confidence guard.
        If hits > max_hits (e.g. 10 rows), defers to SQL Router to avoid polluting LLM context.
        """
        if not entities:
            return ""

        row_data_map: Dict[Tuple[str, int], Dict[str, str]] = {}
        for entity in entities:
            e_clean = entity.strip().lower()
            if not e_clean:
                continue
            raw_tokens = set(re.split(r"[^\w]+", e_clean))
            raw_tokens.add(e_clean)

            for t in raw_tokens:
                if t in self._index:
                    for db_table_name, col_name, row_idx, cell_val in self._index[t]:
                        row_key = (db_table_name, row_idx)
                        row_dict = self._rows.get(row_key)
                        if row_dict:
                            # Only include if extracted entity string is actually in the row values
                            row_vals_concat = " ".join(row_dict.values()).lower()
                            if e_clean in row_vals_concat or any(
                                sub in row_vals_concat
                                for sub in raw_tokens
                                if len(sub) >= 3
                            ):
                                row_data_map[row_key] = row_dict

        # Guard: If entity search matched > max_hits rows, treat as a broad filter and defer to SQL Router
        if len(row_data_map) > max_hits:
            logger.info(
                f"[USIE Cell Index] Entity search matched {len(row_data_map)} rows (> {max_hits} max). Deferring to SQL Router."
            )
            return ""

        if not row_data_map:
            return ""

        # Group matched rows by table name
        table_rows: Dict[str, List[Tuple[int, Dict[str, str]]]] = {}
        for (db_table_name, row_idx), row_dict in row_data_map.items():
            if db_table_name not in table_rows:
                table_rows[db_table_name] = []
            table_rows[db_table_name].append((row_idx, row_dict))

        markdown_blocks = []
        for db_table_name, rows_list in table_rows.items():
            cols = self._table_columns.get(db_table_name, list(rows_list[0][1].keys()))
            hdr_line = "| " + " | ".join(cols) + " |"
            sep_line = "| " + " | ".join(["---"] * len(cols)) + " |"
            data_lines = []
            for r_idx, r_dict in rows_list:
                vals = [r_dict.get(c, "").replace("\n", " ") for c in cols]
                data_lines.append("| " + " | ".join(vals) + " |")

            block = (
                f"### Exact Entity Cell Match (Table: `{db_table_name}`, Matched {len(rows_list)} Row(s)):\n"
                f"{hdr_line}\n{sep_line}\n" + "\n".join(data_lines)
            )
            markdown_blocks.append(block)

        return "\n\n".join(markdown_blocks)


GLOBAL_CELL_INDEX = InvertedCellIndex()

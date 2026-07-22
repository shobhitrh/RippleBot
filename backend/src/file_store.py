"""
Durable source-file store — keeps uploaded files alive on hosts with an
ephemeral filesystem (e.g. Render's free tier, which wipes the disk on every
deploy AND every idle spin-down).

The vector chunks and Tier-C tables already persist in the database; the only
things that die with the disk are the **raw source files** (needed to re-index
and to download) and **companies.json**. This module mirrors those into the
database and rehydrates them to a scratch directory on boot, so the local disk
becomes a disposable cache rather than the source of truth.

Backend mirrors table_store: Postgres when VECTOR_BACKEND=pgvector (the cloud
case this exists for), else a local SQLite file for dev/testing.

Scopes:
  * a company_id  → tenant files under <DOCUMENTS_DIR>/<company_id>/
  * "__global__"  → files at <DOCUMENTS_DIR> root (companies.json)
"""
import os
import sqlite3
import logging
from typing import Optional, List, Tuple

from backend.src import config

logger = logging.getLogger(__name__)

GLOBAL_SCOPE = "__global__"
TABLE = "source_files"


def use_postgres() -> bool:
    return config.VECTOR_BACKEND == "pgvector"


def _sqlite_path() -> str:
    db_dir = os.path.join(config.DOCUMENTS_DIR, "db")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "_files.db")


def _connect():
    """Return (conn, is_pg). Caller closes conn."""
    if use_postgres():
        import psycopg2
        c = psycopg2.connect(config.POSTGRES_URI)
        c.autocommit = True
        return c, True
    return sqlite3.connect(_sqlite_path()), False


def _ensure_table(cur, is_pg: bool):
    if is_pg:
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS {TABLE} ("
            "scope VARCHAR(150) NOT NULL, relpath VARCHAR(1024) NOT NULL, "
            "content BYTEA NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (scope, relpath));"
        )
    else:
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS {TABLE} ("
            "scope TEXT NOT NULL, relpath TEXT NOT NULL, content BLOB NOT NULL, "
            "updated_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (scope, relpath));"
        )


def _dir_for_scope(scope: str) -> str:
    if scope == GLOBAL_SCOPE:
        return config.DOCUMENTS_DIR
    return config.company_documents_dir(scope)


# ── writes ───────────────────────────────────────────────────────────────────
def put(scope: str, relpath: str, data: bytes) -> None:
    """Upsert one file's bytes for a scope."""
    try:
        conn, is_pg = _connect()
        try:
            cur = conn.cursor()
            _ensure_table(cur, is_pg)
            if is_pg:
                import psycopg2
                cur.execute(
                    f"INSERT INTO {TABLE} (scope, relpath, content) VALUES (%s, %s, %s) "
                    f"ON CONFLICT (scope, relpath) DO UPDATE SET content = EXCLUDED.content, "
                    f"updated_at = CURRENT_TIMESTAMP;",
                    (scope, relpath, psycopg2.Binary(data)),
                )
            else:
                cur.execute(
                    f"INSERT OR REPLACE INTO {TABLE} (scope, relpath, content) VALUES (?, ?, ?)",
                    (scope, relpath, sqlite3.Binary(data)),
                )
                conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"file_store.put failed ({scope}/{relpath}): {e}")


def put_global(relpath: str, data: bytes) -> None:
    put(GLOBAL_SCOPE, relpath, data)


def delete(scope: str, relpath: str) -> None:
    try:
        conn, is_pg = _connect()
        try:
            cur = conn.cursor()
            _ensure_table(cur, is_pg)
            ph = "%s" if is_pg else "?"
            cur.execute(f"DELETE FROM {TABLE} WHERE scope = {ph} AND relpath = {ph}", (scope, relpath))
            if not is_pg:
                conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"file_store.delete failed ({scope}/{relpath}): {e}")


def mirror_dir(scope: str, dir_path: str) -> None:
    """
    Make the store for `scope` match what's on disk under `dir_path`: upsert every
    current file (recursively, by relative path) and delete rows for files that no
    longer exist. Call after any operation that adds/removes files for a tenant.
    """
    if not os.path.isdir(dir_path):
        return
    try:
        on_disk = {}
        for root, _dirs, names in os.walk(dir_path):
            # Never mirror the SQLite scratch dir into the store.
            if os.path.basename(root) == "db":
                continue
            for n in names:
                full = os.path.join(root, n)
                rel = os.path.relpath(full, dir_path).replace("\\", "/")
                try:
                    with open(full, "rb") as f:
                        on_disk[rel] = f.read()
                except Exception as e:
                    logger.error(f"file_store.mirror_dir read failed {full}: {e}")

        conn, is_pg = _connect()
        try:
            cur = conn.cursor()
            _ensure_table(cur, is_pg)
            ph = "%s" if is_pg else "?"
            cur.execute(f"SELECT relpath FROM {TABLE} WHERE scope = {ph}", (scope,))
            stored = {r[0] for r in cur.fetchall()}

            for rel, data in on_disk.items():
                if is_pg:
                    import psycopg2
                    cur.execute(
                        f"INSERT INTO {TABLE} (scope, relpath, content) VALUES (%s, %s, %s) "
                        f"ON CONFLICT (scope, relpath) DO UPDATE SET content = EXCLUDED.content, "
                        f"updated_at = CURRENT_TIMESTAMP;",
                        (scope, rel, psycopg2.Binary(data)),
                    )
                else:
                    cur.execute(
                        f"INSERT OR REPLACE INTO {TABLE} (scope, relpath, content) VALUES (?, ?, ?)",
                        (scope, rel, sqlite3.Binary(data)),
                    )
            for rel in stored - set(on_disk):
                cur.execute(f"DELETE FROM {TABLE} WHERE scope = {ph} AND relpath = {ph}", (scope, rel))
            if not is_pg:
                conn.commit()
            logger.info(f"file_store: mirrored {len(on_disk)} file(s) for scope '{scope}'")
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"file_store.mirror_dir failed ({scope}): {e}")


# ── reads / restore ──────────────────────────────────────────────────────────
def get(scope: str, relpath: str) -> Optional[bytes]:
    try:
        conn, is_pg = _connect()
        try:
            cur = conn.cursor()
            _ensure_table(cur, is_pg)
            ph = "%s" if is_pg else "?"
            cur.execute(f"SELECT content FROM {TABLE} WHERE scope = {ph} AND relpath = {ph}", (scope, relpath))
            row = cur.fetchone()
            if not row:
                return None
            return bytes(row[0])
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"file_store.get failed ({scope}/{relpath}): {e}")
        return None


def _restore_rows(rows: List[Tuple[str, str, bytes]]) -> int:
    n = 0
    for scope, rel, content in rows:
        try:
            dest = os.path.join(_dir_for_scope(scope), rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.exists(dest):
                continue  # disk already has it (don't clobber a fresher upload)
            with open(dest, "wb") as f:
                f.write(bytes(content))
            n += 1
        except Exception as e:
            logger.error(f"file_store restore failed {scope}/{rel}: {e}")
    return n


def _fetch(scope: Optional[str]) -> List[Tuple[str, str, bytes]]:
    conn, is_pg = _connect()
    try:
        cur = conn.cursor()
        _ensure_table(cur, is_pg)
        if scope is None:
            cur.execute(f"SELECT scope, relpath, content FROM {TABLE}")
        else:
            ph = "%s" if is_pg else "?"
            cur.execute(f"SELECT scope, relpath, content FROM {TABLE} WHERE scope = {ph}", (scope,))
        return cur.fetchall()
    finally:
        conn.close()


def hydrate_global() -> None:
    """Restore root-level files (companies.json). Fast — call synchronously on boot."""
    try:
        n = _restore_rows(_fetch(GLOBAL_SCOPE))
        if n:
            logger.info(f"file_store: hydrated {n} global file(s) from DB")
    except Exception as e:
        logger.error(f"file_store.hydrate_global failed: {e}")


def hydrate_all() -> None:
    """Restore every stored file to disk. Run before the startup catch-up indexer."""
    try:
        n = _restore_rows(_fetch(None))
        logger.info(f"file_store: hydrated {n} file(s) from DB on boot")
    except Exception as e:
        logger.error(f"file_store.hydrate_all failed: {e}")

"""
Company (tenant) registry.

Persisted as JSON on the same volume as the knowledge base
(<DOCUMENTS_DIR>/companies.json), so it survives redeploys. Holds each tenant's
id, display name, and email domains — the domains drive Fireflies auto-routing
(map a meeting to a company by its attendees' email domains).
"""
import os
import json
import logging
import threading

from backend.src import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# Seed data. "unassigned" is the quarantine tenant for meetings we can't route.
_SEED = [
    {"id": "pinelabs", "name": "Pine Labs", "domains": ["pinelabs.com"]},
]
UNASSIGNED_ID = "unassigned"


def _get_pg_conn():
    uri = config.POSTGRES_URI
    if not uri:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(uri, connect_timeout=5)
        conn.autocommit = True
        return conn
    except Exception as e:
        logger.debug(f"PostgreSQL connection for companies failed: {e}")
        return None

def _init_pg_companies():
    conn = _get_pg_conn()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    id VARCHAR(255) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    domains TEXT[] DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Seed default company if table is empty
            cur.execute("SELECT COUNT(*) FROM companies;")
            if cur.fetchone()[0] == 0:
                for seed in _SEED:
                    cur.execute(
                        "INSERT INTO companies (id, name, domains) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING;",
                        (seed["id"], seed["name"], seed["domains"])
                    )
    except Exception as e:
        logger.error(f"Failed to init companies table in PG: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _load_raw() -> list:
    conn = _get_pg_conn()
    if conn:
        try:
            _init_pg_companies()
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, domains FROM companies ORDER BY created_at ASC;")
                rows = cur.fetchall()
                if rows:
                    return [{"id": r[0], "name": r[1], "domains": r[2] or []} for r in rows]
        except Exception as e:
            logger.error(f"Error loading companies from PG: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # Fallback to local companies.json
    p = _path()
    if not os.path.exists(p):
        _save_raw_json(_SEED)
        return list(_SEED)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else list(_SEED)
    except Exception as e:
        logger.error(f"companies.json unreadable ({e}); using seed")
        return list(_SEED)


def _save_raw_json(companies: list):
    try:
        os.makedirs(config.DOCUMENTS_DIR, exist_ok=True)
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(companies, f, indent=2)
    except Exception as e:
        logger.error(f"Could not save companies.json: {e}")

def _save_raw(companies: list):
    _save_raw_json(companies)
    conn = _get_pg_conn()
    if conn:
        try:
            _init_pg_companies()
            with conn.cursor() as cur:
                for c in companies:
                    cur.execute("""
                        INSERT INTO companies (id, name, domains)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, domains = EXCLUDED.domains;
                    """, (c["id"], c["name"], c.get("domains", [])))
        except Exception as e:
            logger.error(f"Error saving company to PG: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass


def list_companies() -> list:
    """All registered companies (excludes the internal 'unassigned' tenant)."""
    return _load_raw()


def add_company(name: str, domains=None) -> dict:
    """Create (or update) a company. Returns the record."""
    cid = config.normalize_company_id(name)
    domains = [d.strip().lower().lstrip("@") for d in (domains or []) if d and d.strip()]
    with _lock:
        companies = _load_raw()
        for c in companies:
            if c.get("id") == cid:
                # merge any new domains
                merged = sorted(set(c.get("domains", [])) | set(domains))
                c["domains"] = merged
                if name.strip():
                    c["name"] = name.strip()
                _save_raw(companies)
                return c
        record = {"id": cid, "name": name.strip() or cid, "domains": domains}
        companies.append(record)
        _save_raw(companies)
        logger.info(f"Registered company '{cid}' (domains={domains})")
        return record


def update_company(company_id: str, name=None, domains=None):
    """
    Edit an existing company's settings from the UI. The id is immutable (it keys
    all stored data + the X-Company-Id header); only the display name and domain
    list can change. `domains` REPLACES the list (so removing a domain works),
    unlike add_company which merges. Returns the updated record, or None if the
    company doesn't exist.
    """
    cid = config.normalize_company_id(company_id)
    with _lock:
        companies = _load_raw()
        for c in companies:
            if c.get("id") == cid:
                if name is not None and str(name).strip():
                    c["name"] = str(name).strip()
                if domains is not None:
                    c["domains"] = sorted(
                        {d.strip().lower().lstrip("@") for d in domains if d and d.strip()}
                    )
                _save_raw(companies)
                logger.info(f"Updated company '{cid}' (domains={c.get('domains')})")
                return c
    return None


def company_name(company_id: str) -> str:
    """Human-readable display name for a company id (falls back to the id)."""
    if not company_id:
        return "this customer"
    cid = config.normalize_company_id(company_id)
    for c in _load_raw():
        if c.get("id") == cid:
            return c.get("name") or cid
    return cid


def company_for_domain(domain: str):
    """Return the company id whose domains include this email domain, or None."""
    if not domain:
        return None
    domain = domain.strip().lower().lstrip("@")
    for c in _load_raw():
        if domain in [d.lower() for d in c.get("domains", [])]:
            return c["id"]
    return None


def resolve_company_from_emails(emails) -> str:
    """
    Pick the tenant for a meeting from its attendee emails, by domain match.
    Returns a company id, or UNASSIGNED_ID when no domain matches (never guess —
    an unmatched meeting is quarantined, not filed into a wrong tenant).
    """
    seen = set()
    for e in emails or []:
        if not e or "@" not in str(e):
            continue
        dom = str(e).split("@")[-1].strip().lower()
        cid = company_for_domain(dom)
        if cid:
            seen.add(cid)
    if len(seen) == 1:
        return seen.pop()
    # 0 matches → unassigned; >1 distinct companies → ambiguous → unassigned too.
    return UNASSIGNED_ID

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


def _path() -> str:
    return os.path.join(config.DOCUMENTS_DIR, "companies.json")


def _load_raw() -> list:
    p = _path()
    if not os.path.exists(p):
        _save_raw(_SEED)
        return list(_SEED)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else list(_SEED)
    except Exception as e:
        logger.error(f"companies.json unreadable ({e}); using seed")
        return list(_SEED)


def _save_raw(companies: list):
    try:
        os.makedirs(config.DOCUMENTS_DIR, exist_ok=True)
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(companies, f, indent=2)
    except Exception as e:
        logger.error(f"Could not save companies.json: {e}")


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

"""
Document360 → Voyage ingest (PRD §16 / M4).

Pulls the full help-center catalogue via the Document360 API, strips each article
to text, and writes one Markdown file per article into the SHARED help-center
company store (config.HELP_CENTER_COMPANY_ID). It then triggers RippleBot's existing
indexing pipeline, so the articles are chunked + embedded with Voyage exactly like
any uploaded document — no bespoke embedding code, reusing the battle-tested path.

After this runs, search_help_center returns real semantic answers (it queries the
shared store). Re-run whenever the help center changes; unchanged files are skipped
by the engine's content-hash check, so re-runs are cheap.

Run:
    python -m backend.src.agentic.ingest_d360

Requires DOCUMENT360_API_KEY in .env (optionally DOCUMENT360_PUBLIC_BASE_URL for
citation links and DOCUMENT360_PROJECT_VERSION_ID to pin a version).
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from backend.src import config

logger = logging.getLogger(__name__)

_MAX_ARTICLE_CHARS = 50_000
_HTTP_TIMEOUT = 30.0


# --------------------------------------------------------------------------- #
# Document360 API helpers (ported from PIA's ingest.py — same endpoints).      #
# --------------------------------------------------------------------------- #
def _headers() -> dict:
    return {"api_token": config.DOCUMENT360_API_KEY or "", "Accept": "application/json"}


def _public_url(slug: str) -> str:
    base = config.DOCUMENT360_PUBLIC_BASE_URL
    return f"{base}/docs/{slug}" if base and slug else ""


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<\s*br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = (
        text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
        .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _walk_categories(cats: list, parent_path: str = "") -> list[dict]:
    out: list[dict] = []
    for cat in cats or []:
        title = (cat.get("title") or cat.get("name") or "").strip()
        path = f"{parent_path}/{title}" if parent_path else title
        for art in cat.get("articles") or []:
            out.append({
                "id": str(art.get("id") or art.get("article_id") or ""),
                "title": (art.get("title") or "Untitled").strip(),
                "slug": (art.get("slug") or "").strip(),
                "category": path,
            })
        sub = cat.get("child_categories") or cat.get("children") or []
        if sub:
            out.extend(_walk_categories(sub, path))
    return out


def _safe(text: str) -> str:
    return re.sub(r"[^\w\-]", "_", text)[:80]


def _resolve_project_version(client) -> str:
    pv = config.DOCUMENT360_PROJECT_VERSION_ID
    if pv:
        return pv
    r = client.get(f"{config.D360_API_BASE}/v2/projectversions")
    r.raise_for_status()
    payload = r.json()
    versions = payload.get("data") if isinstance(payload, dict) else payload
    if not versions:
        raise RuntimeError("No Document360 project versions visible to this token.")
    return versions[0].get("id") or versions[0].get("version_id")


# --------------------------------------------------------------------------- #
# Main ingest                                                                  #
# --------------------------------------------------------------------------- #
def ingest() -> dict:
    """Fetch → write .md files → index. Returns a summary dict."""
    if not config.DOCUMENT360_API_KEY:
        return {"ok": False, "error": "DOCUMENT360_API_KEY not set."}

    try:
        import httpx
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"httpx not installed: {e}"}

    dest_company = config.HELP_CENTER_COMPANY_ID
    docs_dir = Path(config.company_documents_dir(dest_company)) / "_help_center"
    docs_dir.mkdir(parents=True, exist_ok=True)

    fetched = failed = 0
    with httpx.Client(timeout=_HTTP_TIMEOUT, headers=_headers()) as client:
        pv = _resolve_project_version(client)
        logger.info("D360 project version: %s", pv)

        r = client.get(f"{config.D360_API_BASE}/v2/projectversions/{pv}/categories")
        r.raise_for_status()
        payload = r.json()
        cats = payload.get("data") if isinstance(payload, dict) else (payload or [])
        articles = _walk_categories(cats)
        logger.info("Found %d articles in catalogue.", len(articles))

        for art in articles:
            art_id, title, slug, category = art["id"], art["title"], art["slug"], art["category"]
            if not art_id:
                failed += 1
                continue
            try:
                ar = client.get(f"{config.D360_API_BASE}/v2/articles/{art_id}")
                if ar.status_code == 404:
                    ar = client.get(f"{config.D360_API_BASE}/v1/Articles/{art_id}")
                ar.raise_for_status()
                data = ar.json()
            except Exception as e:
                logger.warning("fetch failed for '%s' (%s): %s", title, art_id, e)
                failed += 1
                continue

            body = data.get("data") if isinstance(data, dict) else None
            body = body or data or {}
            html = body.get("html_content") or body.get("content") or ""
            text = _strip_html(html) if "<" in html else (html or "").strip()
            if len(text) > _MAX_ARTICLE_CHARS:
                text = text[:_MAX_ARTICLE_CHARS] + "\n[... truncated]"

            url = _public_url(slug)
            out = docs_dir / f"{_safe(category)}__{_safe(title)}_{art_id}.md"
            with out.open("w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n")
                f.write(f"**Category:** {category}\n")
                if url:
                    f.write(f"**URL:** {url}\n")
                f.write("\n---\n\n")
                f.write(text + "\n")
            fetched += 1
            time.sleep(0.05)  # be polite to the API

    # Embed via RippleBot's existing pipeline (Voyage) — incremental by content hash.
    indexed = False
    try:
        from backend.src.rag_engine import get_engine
        engine = get_engine(dest_company, required=False)
        if engine is not None:
            engine.build_index(force_rebuild=False)
            indexed = True
    except Exception as e:
        logger.error("indexing help-center store failed: %s", e)

    return {
        "ok": True,
        "store": dest_company,
        "articles_fetched": fetched,
        "articles_failed": failed,
        "indexed": indexed,
        "dir": str(docs_dir),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("\n=== Document360 → Voyage ingest ===\n")
    summary = ingest()
    if not summary.get("ok"):
        print("FAILED:", summary.get("error"))
        raise SystemExit(1)
    print(
        f"Done. Fetched {summary['articles_fetched']} articles "
        f"({summary['articles_failed']} failed) into store '{summary['store']}'. "
        f"Indexed: {summary['indexed']}."
    )
    print("search_help_center will now return semantic answers.")

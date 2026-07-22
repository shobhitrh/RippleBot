"""
Vector-store engine factory (multi-tenant).

Returns a cached RAG engine PER COMPANY, regardless of which vector store is
configured (ChromaDB embedded by default, or pgvector for cloud/Railway).

Isolation:
  * ChromaDB  -> one collection per company ("org_<id>_documents").
  * pgvector  -> shared tables with a company_id column; every query filtered.
Each company also reads/writes its own documents subdirectory.

Reliability:
  * One engine per (company) is cached and its connection reused, so we don't
    reconnect + migrate on every request.
  * Reconnect attempts while the store is down are throttled per company, and the
    down-state is logged once. Callers can request a graceful ``None``.
"""
import time
import logging
import threading

from backend.src import config

logger = logging.getLogger(__name__)

# Per-company cached engine state
_engines = {}                 # company_id -> engine
_last_attempt = {}            # company_id -> monotonic ts of last connect attempt
_down_logged = set()          # company_ids whose down-state we've already logged
_lock = threading.Lock()
_COOLDOWN_SEC = 10.0


class EngineUnavailable(RuntimeError):
    """Raised (when required=True) if the vector store cannot be reached."""
    pass


def _build_engine(company_id: str):
    """Construct the configured engine for one company, wiring per-tenant paths."""
    documents_dir = config.company_documents_dir(company_id)
    backend = config.VECTOR_BACKEND
    if backend == "pgvector":
        from rag_migration_kit.rag_pgvector import RAGEngine
        return RAGEngine(
            connection_uri=config.POSTGRES_URI,
            company_id=company_id,
            documents_dir=documents_dir,
        )

    # Default: ChromaDB (embedded, no server required)
    from rag_migration_kit.rag_chromadb import RAGEngine
    return RAGEngine(
        persist_directory=config.CHROMA_DIR,
        company_id=company_id,
        documents_dir=documents_dir,
    )


def get_engine(company_id: str = None, required: bool = True):
    """
    Return the cached engine for ``company_id`` (defaults to DEFAULT_COMPANY_ID),
    (re)connecting lazily and throttling reconnects while the store is down.

    :param required: when False, returns ``None`` on failure instead of raising.
    """
    cid = config.normalize_company_id(company_id or config.DEFAULT_COMPANY_ID)

    with _lock:
        eng = _engines.get(cid)
        # Fast path: healthy cached engine for this tenant.
        if eng is not None and eng.is_connected():
            return eng

        now = time.monotonic()
        if eng is not None and (now - _last_attempt.get(cid, 0.0)) < _COOLDOWN_SEC:
            if required:
                raise EngineUnavailable(
                    f"{config.VECTOR_BACKEND} vector store unavailable (retry throttled)."
                )
            return None

        _last_attempt[cid] = now
        try:
            if eng is None:
                eng = _build_engine(cid)
                _engines[cid] = eng
            else:
                eng.reconnect()
            if cid in _down_logged:
                logger.info(f"✅ RAG engine reconnected ({config.VECTOR_BACKEND}, tenant='{cid}').")
                _down_logged.discard(cid)
            return eng
        except Exception as e:
            first_line = str(e).splitlines()[0] if str(e) else repr(e)
            if cid not in _down_logged:
                logger.warning(
                    f"Vector store '{config.VECTOR_BACKEND}' unavailable for tenant '{cid}' — "
                    f"RAG features degraded until reachable. Reason: {first_line}"
                )
                _down_logged.add(cid)
            if required:
                raise EngineUnavailable(first_line)
            return None


def engine_status(company_id: str = None) -> dict:
    """Lightweight status snapshot for the health endpoint (never raises)."""
    eng = get_engine(company_id, required=False)
    if eng is None:
        return {"status": "error", "backend": config.VECTOR_BACKEND, "doc_count": 0, "chunk_count": 0}
    try:
        if hasattr(eng, "get_counts"):
            dc, cc = eng.get_counts()
        else:
            dc, cc = eng.get_doc_count(), eng.get_chunk_count()
        return {
            "status": "connected",
            "backend": config.VECTOR_BACKEND,
            "doc_count": dc,
            "chunk_count": cc,
        }
    except Exception as e:
        return {"status": "error", "backend": config.VECTOR_BACKEND, "error": str(e)}

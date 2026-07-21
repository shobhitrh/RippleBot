"""
Vector-store engine factory.

Provides a single, cached RAG engine instance for the whole backend, regardless
of which vector store is configured (ChromaDB embedded by default, or pgvector).

Why this exists:
  * The routers used to construct a fresh engine on every HTTP request, which
    meant a full (re)connect + schema migration several times a second — and,
    when the store was unavailable, a full traceback on every poll.
  * This factory caches one engine, reuses its connection, throttles reconnect
    attempts, and logs the down-state exactly once. Callers can ask for a
    graceful ``None`` instead of an exception so the API degrades cleanly.
"""
import time
import logging
import threading
from typing import Optional

from backend.src import config

logger = logging.getLogger(__name__)

# Cached singleton state
_engine = None
_lock = threading.Lock()
_last_attempt = 0.0
_COOLDOWN_SEC = 10.0
_down_logged = False


class EngineUnavailable(RuntimeError):
    """Raised (when required=True) if the vector store cannot be reached."""
    pass


def _build_engine():
    """Import and construct the configured engine, wiring the correct paths."""
    backend = config.VECTOR_BACKEND
    if backend == "pgvector":
        from rag_migration_kit.rag_pgvector import RAGEngine, Config as EngineConfig
        EngineConfig.set_documents_dir(config.DOCUMENTS_DIR)
        return RAGEngine(connection_uri=config.POSTGRES_URI)

    # Default: ChromaDB (embedded, no server required)
    from rag_migration_kit.rag_chromadb import RAGEngine, Config as EngineConfig
    EngineConfig.set_documents_dir(config.DOCUMENTS_DIR)
    EngineConfig.CHROMA_DIR = config.CHROMA_DIR
    return RAGEngine(persist_directory=config.CHROMA_DIR)


def get_engine(required: bool = True):
    """
    Return the shared engine, (re)connecting lazily. Reconnect attempts while the
    store is down are throttled to once per cooldown window so we neither hammer
    nor spam logs.

    :param required: when False, returns ``None`` on failure instead of raising.
    """
    global _engine, _last_attempt, _down_logged

    with _lock:
        # Fast path: healthy cached engine.
        if _engine is not None and _engine.is_connected():
            return _engine

        now = time.monotonic()
        if _engine is not None and (now - _last_attempt) < _COOLDOWN_SEC:
            # Still within the cooldown after a recent failure — stay quiet.
            if required:
                raise EngineUnavailable(
                    f"{config.VECTOR_BACKEND} vector store unavailable (retry throttled)."
                )
            return None

        _last_attempt = now
        try:
            if _engine is None:
                _engine = _build_engine()
            else:
                _engine.reconnect()
            if _down_logged:
                logger.info(f"✅ RAG engine reconnected ({config.VECTOR_BACKEND}).")
            _down_logged = False
            return _engine
        except Exception as e:
            first_line = str(e).splitlines()[0] if str(e) else repr(e)
            if not _down_logged:
                logger.warning(
                    f"Vector store '{config.VECTOR_BACKEND}' unavailable — RAG features "
                    f"degraded until it is reachable. Reason: {first_line}"
                )
                _down_logged = True
            if required:
                raise EngineUnavailable(first_line)
            return None


def engine_status() -> dict:
    """Lightweight status snapshot for the health endpoint (never raises)."""
    eng = get_engine(required=False)
    if eng is None:
        return {"status": "error", "backend": config.VECTOR_BACKEND, "doc_count": 0, "chunk_count": 0}
    try:
        return {
            "status": "connected",
            "backend": config.VECTOR_BACKEND,
            "doc_count": eng.get_doc_count(),
            "chunk_count": eng.get_chunk_count(),
        }
    except Exception as e:
        return {"status": "error", "backend": config.VECTOR_BACKEND, "error": str(e)}

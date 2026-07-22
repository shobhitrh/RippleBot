import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from backend.src import config
from backend.src.watcher import start_watcher, stop_watcher
from backend.src.router import webhook, document, chat, company

# Set up logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Filter out successful polling logs from uvicorn access log to prevent console spam
class PollingLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        # Filter out 200 OK access logs for /api/health and /api/documents
        if ("GET /api/health" in msg or "GET /api/documents" in msg) and " 200 " in msg:
            return False
        return True

# Apply filter to uvicorn.access logger, the parent uvicorn logger, and all handlers
for logger_name in ("uvicorn.access", "uvicorn", ""):
    l = logging.getLogger(logger_name)
    l.addFilter(PollingLogFilter())
    for h in l.handlers:
        h.addFilter(PollingLogFilter())

def _index_existing_documents():
    """Index files already present in each tenant's folder (startup catch-up)."""
    try:
        # First restore any source files from the durable store (hosts with an
        # ephemeral disk, e.g. Render free tier, wipe the filesystem on restart).
        from backend.src import file_store
        file_store.hydrate_all()

        from backend.src.rag_engine import get_engine
        base = config.DOCUMENTS_DIR
        if not os.path.isdir(base):
            return
        for entry in sorted(os.listdir(base)):
            tenant_dir = os.path.join(base, entry)
            if not os.path.isdir(tenant_dir) or entry == "db":
                continue
            has_docs = any(
                os.path.isfile(os.path.join(tenant_dir, f))
                and not f.endswith(".metadata.json")
                and not f.startswith(".")
                for f in os.listdir(tenant_dir)
            )
            if not has_docs:
                continue
            logger.info(f"Startup indexing: catching up tenant '{entry}'…")
            try:
                engine = get_engine(entry, required=False)
                if engine is not None:
                    engine.build_index(force_rebuild=False)
            except Exception:
                logger.error(f"Startup indexing failed for tenant '{entry}'", exc_info=True)
    except Exception:
        logger.error("Startup indexing sweep failed", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Re-apply log filter inside lifespan to ensure we catch runtime uvicorn handlers
    for logger_name in ("uvicorn.access", "uvicorn", ""):
        l = logging.getLogger(logger_name)
        l.addFilter(PollingLogFilter())
        for h in l.handlers:
            h.addFilter(PollingLogFilter())

    logger.info("Starting FastAPI application...")
    try:
        start_watcher()
    except Exception as e:
        logger.error(f"Failed to start watchdog folder watcher: {e}")

    # Self-heal on boot: index any files already on disk (e.g. restored from a
    # persistent volume after a redeploy). Incremental — unchanged files are
    # skipped via hash check, so this is a no-op when everything is indexed.
    # Runs in a daemon thread so startup/healthchecks aren't blocked.
    import threading as _threading
    _threading.Thread(target=_index_existing_documents, daemon=True).start()

    yield
    
    logger.info("Shutting down FastAPI application...")
    try:
        stop_watcher()
    except Exception as e:
        logger.error(f"Failed to stop watchdog folder watcher cleanly: {e}")

app = FastAPI(
    title="RippleBot Knowledge Base & RAG Pipeline API",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook.router, prefix="/api")
app.include_router(document.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(company.router, prefix="/api")

@app.get("/")
async def root():
    """Simple root healthcheck endpoint."""
    return {
        "status": "healthy",
        "service": "RippleBot RAG Backend Engine",
        "version": "1.0.0"
    }

@app.get("/api/health")
async def health_check(x_company_id: str = Header(default=config.DEFAULT_COMPANY_ID, alias="X-Company-Id")):
    """
    Live system health check for the requesting tenant. Pings the vector store,
    checks the company's knowledge_base folder and the watcher state.
    """
    import time
    from backend.src.rag_engine import engine_status

    company_id = config.normalize_company_id(x_company_id)
    result = {
        "api": {"status": "online", "latency_ms": 0},
        "vector_db": {"status": "unknown", "doc_count": 0, "chunk_count": 0},
        "watcher": {"status": "unknown"},
        "knowledge_base_dir": {"status": "unknown", "file_count": 0},
        "company_id": company_id,
    }

    # Check the configured vector store for this tenant (never raises)
    t0 = time.monotonic()
    result["vector_db"] = engine_status(company_id)
    result["vector_db"]["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)

    # Check watcher
    from backend.src.watcher import _observer
    result["watcher"] = {
        "status": "running" if (_observer and _observer.is_alive()) else "stopped"
    }

    # Check the company's knowledge_base folder
    kb_dir = config.company_documents_dir(company_id)
    try:
        files = [f for f in os.listdir(kb_dir) if os.path.isfile(os.path.join(kb_dir, f))]
        result["knowledge_base_dir"] = {
            "status": "accessible",
            "file_count": len(files),
            "path": kb_dir,
        }
    except Exception as e:
        result["knowledge_base_dir"] = {"status": "error", "error": str(e)}

    result["api"]["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return result

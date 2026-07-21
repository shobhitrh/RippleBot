import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.src import config
from backend.src.watcher import start_watcher, stop_watcher
from backend.src.router import webhook, document, chat

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

@app.get("/")
async def root():
    """Simple root healthcheck endpoint."""
    return {
        "status": "healthy",
        "service": "RippleBot RAG Backend Engine",
        "version": "1.0.0"
    }

@app.get("/api/health")
async def health_check():
    """
    Live system health check. Pings Postgres and checks the knowledge_base
    folder and watcher state to return real-time service status.
    """
    import time
    from backend.src.rag_engine import engine_status

    result = {
        "api": {"status": "online", "latency_ms": 0},
        "vector_db": {"status": "unknown", "doc_count": 0, "chunk_count": 0},
        "watcher": {"status": "unknown"},
        "knowledge_base_dir": {"status": "unknown", "file_count": 0},
    }

    # Check the configured vector store (backend-agnostic, never raises)
    t0 = time.monotonic()
    result["vector_db"] = engine_status()
    result["vector_db"]["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)

    # Check watcher
    from backend.src.watcher import _observer
    result["watcher"] = {
        "status": "running" if (_observer and _observer.is_alive()) else "stopped"
    }

    # Check knowledge_base folder
    kb_dir = config.DOCUMENTS_DIR
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

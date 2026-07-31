import os
import re
from pathlib import Path
from dotenv import load_dotenv

# Base Paths
BACKEND_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = BACKEND_DIR.parent

# Load environment from known locations regardless of the launch directory.
# Load the workspace-root .env first, then backend/.env (backend wins on overlap),
# then any CWD .env. This ensures keys like GROQ_API_KEY2/3 and GEMINI_API_KEY are
# picked up whether uvicorn is started from the repo root or from backend/.
load_dotenv(WORKSPACE_ROOT / ".env", override=True)
load_dotenv(BACKEND_DIR / ".env", override=True)
load_dotenv(override=True)

# Target folder for staged/uploaded files
DOCUMENTS_DIR = os.getenv("DOCUMENTS_DIR")
if not DOCUMENTS_DIR:
    DOCUMENTS_DIR = str(BACKEND_DIR / "knowledge_base")
else:
    # Resolve relative to workspace root if it's relative
    p = Path(DOCUMENTS_DIR)
    if not p.is_absolute():
        DOCUMENTS_DIR = str(WORKSPACE_ROOT / DOCUMENTS_DIR)

# Ensure directory exists
Path(DOCUMENTS_DIR).mkdir(parents=True, exist_ok=True)

# Vector store backend selection: "chroma" (embedded, zero-setup, default) or
# "pgvector" (PostgreSQL — needs a running server / connection string).
VECTOR_BACKEND = os.getenv("VECTOR_BACKEND", "chroma").strip().lower()

# ChromaDB persistence directory (embedded local store — no server required).
CHROMA_DIR = os.getenv("CHROMA_DIR")
if not CHROMA_DIR:
    CHROMA_DIR = str(BACKEND_DIR / "chroma_db")
else:
    p = Path(CHROMA_DIR)
    if not p.is_absolute():
        CHROMA_DIR = str(WORKSPACE_ROOT / CHROMA_DIR)

# Database Credentials (only used when VECTOR_BACKEND == "pgvector")
POSTGRES_URI = os.getenv("POSTGRES_URI") or os.getenv("DATABASE_URL")
# Default local development fallback if not provided
if not POSTGRES_URI:
    POSTGRES_URI = "postgresql://postgres:postgres@localhost:5432/argushr"

# API Keys
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY2")
FIREFLIES_API_KEY = os.getenv("FIREFLIES_API_KEY")
# Optional shared secret to authenticate Fireflies webhook calls. If set, the
# webhook URL must include ?token=<this> (or an X-Webhook-Token header).
FIREFLIES_WEBHOOK_SECRET = os.getenv("FIREFLIES_WEBHOOK_SECRET")


def _valid_key(k):
    """Treat empty / placeholder values (e.g. 'your_..._here') as unset."""
    return bool(k) and not k.strip().lower().startswith("your_")


# Groq keys, tried in order for fallback (GROQ_API_KEY -> GROQ_API_KEY2 -> GROQ_API_KEY3 -> GROQ_API_KEY4 ...).
_groq_env_keys = [os.getenv("GROQ_API_KEY")] + [os.getenv(f"GROQ_API_KEY{i}") for i in range(2, 11)]
GROQ_API_KEYS = [k for k in _groq_env_keys if _valid_key(k)]
# Backwards-compatible single-key alias (first valid Groq key).
GROQ_API_KEY = GROQ_API_KEYS[0] if GROQ_API_KEYS else None

# Final fallback LLM after all Groq keys fail.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") if _valid_key(os.getenv("GEMINI_API_KEY")) else None

# ---------------- MULTI-TENANCY ----------------
# Every request is scoped to a company (tenant) via the "X-Company-Id" header.
# Requests without one fall back to DEFAULT_COMPANY_ID so single-tenant / local
# use keeps working unchanged.
DEFAULT_COMPANY_ID = os.getenv("DEFAULT_COMPANY_ID", "default")


def normalize_company_id(company_id: str) -> str:
    """
    Sanitize a tenant id into a safe slug usable in file paths, Chroma collection
    names, and SQL values: lowercase, alnum + underscore/hyphen only.
    """
    cid = (company_id or "").strip().lower()
    cid = re.sub(r"[^a-z0-9_-]+", "_", cid).strip("_-")
    return cid or DEFAULT_COMPANY_ID


def company_documents_dir(company_id: str) -> str:
    """Per-tenant upload directory: <knowledge_base>/<company_id>/."""
    path = os.path.join(DOCUMENTS_DIR, normalize_company_id(company_id))
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


# ---------------- AGENTIC LAYER (PIA UNIFICATION — see PRD §18/§19) ----------------
# The agentic orchestration brain (Claude tool-use loop) is entirely ADDITIVE and
# OFF BY DEFAULT. With AGENTIC_MODE unset, RippleBot behaves exactly as before —
# the existing /api/chat/query pipeline is never touched. Flip AGENTIC_MODE=on
# (and plug in the keys below) to activate the opt-in /api/agentic/query endpoint.
#
# Nothing here raises at import time: every heavy dependency (anthropic, pymysql,
# httpx) is imported lazily inside the agentic module, so a missing package or a
# missing key degrades gracefully instead of crashing the app.

AGENTIC_MODE = os.getenv("AGENTIC_MODE", "off").strip().lower() in ("on", "1", "true", "yes")

# -- Claude (the orchestration brain). Absent key => agentic loop reports "not configured".
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") if _valid_key(os.getenv("ANTHROPIC_API_KEY")) else None
# Orchestration model for complex/multi-source queries (see PRD §19.1 tiers).
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5").strip()
# Cheaper model for the simple/complex classifier + light synthesis.
ANTHROPIC_CLASSIFIER_MODEL = os.getenv("ANTHROPIC_CLASSIFIER_MODEL", "claude-haiku-4-5").strip()
# Agentic loop bounds (mirrors PIA's server.py).
MAX_TOOL_TURNS = int(os.getenv("MAX_TOOL_TURNS", "10"))
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "8"))

# -- Live operational MySQL (PIA crown jewel). Absent => query_live_database tool is a no-op stub.
LIVE_DB_HOST = os.getenv("DB_HOST") or None
LIVE_DB_PORT = int(os.getenv("DB_PORT", "3306"))
LIVE_DB_USER = os.getenv("DB_USER") or None
LIVE_DB_PASSWORD = os.getenv("DB_PASSWORD") or None
LIVE_DB_NAME = os.getenv("DB_NAME") or None
# Row cap returned to the model, and the append-only audit log path.
LIVE_DB_MAX_ROWS = int(os.getenv("DB_MAX_ROWS", "50"))
DB_AUDIT_LOG = os.getenv("DB_AUDIT_LOG") or str(BACKEND_DIR / "db_audit.log")


def live_db_configured() -> bool:
    """True only when enough creds exist to attempt a live (read-only) MySQL connection."""
    return bool(LIVE_DB_HOST and LIVE_DB_USER and LIVE_DB_NAME)


# -- Document360 help center. Absent => search_help_center tool is a no-op stub.
DOCUMENT360_API_KEY = os.getenv("DOCUMENT360_API_KEY") if _valid_key(os.getenv("DOCUMENT360_API_KEY")) else None
DOCUMENT360_PUBLIC_BASE_URL = (os.getenv("DOCUMENT360_PUBLIC_BASE_URL") or "").rstrip("/")
DOCUMENT360_PROJECT_VERSION_ID = os.getenv("DOCUMENT360_PROJECT_VERSION_ID") or None
D360_API_BASE = os.getenv("DOCUMENT360_API_BASE", "https://apihub.document360.io").rstrip("/")
# Help-center articles are product-wide, not tenant-specific, so they are ingested
# ONCE into a single reserved company store and every tenant's search_help_center
# queries that shared store (avoids embedding the same articles 35× — PRD §17.2).
HELP_CENTER_COMPANY_ID = normalize_company_id(os.getenv("HELP_CENTER_COMPANY_ID", "help_center"))

# -- Supabase auth (tenant scoping). Absent => agentic endpoint falls back to the
#    X-Company-Id header exactly like the rest of RippleBot (no enforcement change).
SUPABASE_URL = os.getenv("SUPABASE_URL") or None
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET") or None


def anthropic_configured() -> bool:
    """True when the Claude orchestration brain can be instantiated."""
    return bool(ANTHROPIC_API_KEY)


# Server Config
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
# Comma-separated allowed origins. On Railway set CORS_ORIGINS to your frontend URL.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

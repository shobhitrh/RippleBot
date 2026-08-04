# RippleBot — single self-contained image (mirrors trampolinetech/infosec-tool's
# pattern, adapted for an SSR frontend). Exposes ONE port (8000); the VM's shared
# nginx just proxies subdomain → container:8000.
#
# Inside the container, supervisord runs three processes and an internal nginx
# unifies them on :8000:
#   /api  → FastAPI/uvicorn      (127.0.0.1:8001)
#   /     → TanStack SSR (Node)  (127.0.0.1:3000)
#
# The frontend is SSR (TanStack Start), so we build it with Nitro's node-server
# preset and run the Node output — no risky static-SPA conversion.

# ── Stage 1: build the SSR frontend ───────────────────────────────────────────
FROM node:20-slim AS frontend-builder
WORKDIR /build
COPY knowledge-navigator/package.json knowledge-navigator/package-lock.json* ./
RUN npm ci || npm install
COPY knowledge-navigator/ ./
# Build the Node server output (portable: unset NITRO_PRESET → Vercel later)
ENV NITRO_PRESET=node-server
RUN npm run build

# ── Stage 2: runtime (Python backend + Node SSR + nginx + supervisord) ─────────
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential nginx supervisor curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps (backend)
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Backend source + the RAG migration kit it imports
COPY backend/ ./backend/
COPY rag_migration_kit/ ./rag_migration_kit/

# Built SSR frontend (Nitro node-server output)
COPY --from=frontend-builder /build/.output ./frontend/.output

# Process orchestration + internal reverse proxy
COPY deploy/supervisord.conf /etc/supervisor/conf.d/ripplebot.conf
COPY deploy/nginx-container.conf /etc/nginx/nginx.conf

# Data (Chroma vectors + uploaded files + per-tenant SQLite) lives on a volume
RUN mkdir -p /app/data
ENV DOCUMENTS_DIR=/app/data/knowledge_base \
    CHROMA_DIR=/app/data/chroma_db \
    PYTHONUNBUFFERED=1 \
    PORT=8001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=40s \
  CMD curl -sf http://localhost:8000/api/health >/dev/null || exit 1

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/ripplebot.conf"]

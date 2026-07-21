# Deploying RippleBot (multi-tenant) on Railway + Postgres/pgvector

RippleBot is now **multi-tenant**: every request carries an `X-Company-Id` header and
each company gets fully isolated storage:

- **pgvector**: shared `documents`/`chunks` tables with a `company_id` column; every
  query is filtered by it.
- **structured tables (SQL router)**: a separate SQLite file per company at
  `backend/knowledge_base/db/<company_id>_tables.db`.
- **uploads**: stored under `backend/knowledge_base/<company_id>/`.

New companies need **zero setup** — the backend provisions storage the first time it
sees a new `X-Company-Id`.

---

## 1. Backend → Railway (API + LLM)

You already created a Railway PostgreSQL. Now:

1. **New Project → Deploy from GitHub repo** (or `railway up` from this folder).
   Railway auto-detects Python via the root `requirements.txt` and uses `railway.json`
   (start command: `uvicorn backend.src.main:app --host 0.0.0.0 --port $PORT`).

2. **Attach the database**: in the API service → **Variables → Add Reference →**
   select your Postgres's `DATABASE_URL`. (The app reads `POSTGRES_URI` **or**
   `DATABASE_URL`, so a reference is enough.)

3. **Set the remaining variables** on the API service:
   ```
   VECTOR_BACKEND=pgvector
   VOYAGE_API_KEY2=<your voyage key>
   GROQ_API_KEY=<key>          # optional GROQ_API_KEY2 / GROQ_API_KEY3 for fallback
   GEMINI_API_KEY=<key>        # final LLM fallback
   CORS_ORIGINS=https://<your-frontend-domain>
   # EMBED_DIM is auto-detected (voyage-4-large = 1024); only set to override.
   ```

4. **Deploy.** On first boot the app connects and **auto-creates** the `vector`
   extension + `documents`/`chunks` tables (this is why they were empty before —
   nothing had connected yet). Check the deploy logs for
   `✅ PostgreSQL database initialized`. The `/api/health` healthcheck should go green.

> pgvector is bundled in Railway's Postgres image; `CREATE EXTENSION vector` runs
> automatically. If your plan disables it, enable pgvector from the DB's settings.

### Loading data
There's no shared filesystem across Railway deploys, so ingest via the API (which
also tags each doc with its tenant):
```bash
curl -X POST https://<api>/api/documents/upload \
  -H "X-Company-Id: pinelabs" \
  -F "file=@Pine_Labs_Handover_Sheet.xlsx" \
  -F "department=Sales" -F "category=Other"
```
Indexing starts automatically; poll `GET /api/documents` (same header) for status.

---

## 2. Frontend → Vercel / Netlify / Railway static

The frontend (`knowledge-navigator/`) is already cloud-ready — it reads the backend
URL from `VITE_API_URL` and sends `X-Company-Id` on every call.

1. Deploy `knowledge-navigator/` (build: `npm run build`).
2. Set env var: `VITE_API_URL=https://<your-railway-api-domain>`.
3. Set the API's `CORS_ORIGINS` to the frontend's domain (step 1.3 above).

Users pick their company from the selector in the top bar; chat history is kept
separately per company.

---

## 3. Verifying isolation (do this once on a staging DB)

```bash
# Tenant A
curl -s -X POST https://<api>/api/chat/query -H "X-Company-Id: pinelabs" \
  -H "Content-Type: application/json" -d '{"query":"summarize the handover"}'

# Tenant B (fresh, empty) must NOT see tenant A's data
curl -s -X POST https://<api>/api/chat/query -H "X-Company-Id: techcorp" \
  -H "Content-Type: application/json" -d '{"query":"summarize the handover"}'
```
Tenant B should reply that it has nothing indexed.

---

## Local development
Defaults to embedded ChromaDB (no DB needed):
```bash
uvicorn backend.src.main:app --reload --port 8000     # VECTOR_BACKEND defaults to chroma
```
Set `VECTOR_BACKEND=pgvector` + `POSTGRES_URI=...` to test the Railway path locally.

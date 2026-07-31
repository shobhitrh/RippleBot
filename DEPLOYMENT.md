# RippleBot — Deployment & Operations Guide

Everything needed to run RippleBot in the cloud: the architecture, how we use
**Render + Vercel + Neon**, exact deploy steps, the mistakes we hit and how we
fixed them, environment variables, and troubleshooting.

> **Platform note:** RippleBot deploys on **Render** (backend) + **Vercel**
> (frontend) + **Neon** (managed Postgres/pgvector). We previously used Railway;
> those files (`railway.json`, `RAILWAY_DEPLOY.md`, `Procfile`) have been removed.
> The Render blueprint is `render.yaml` at the repo root.

---

## 1. Architecture at a glance

```
                Browser (users pick a Company in the top bar)
                                 │  every request carries  X-Company-Id
                                 ▼
        ┌───────────────────────────────────────────────┐
        │  Frontend — Vercel                             │
        │  TanStack Start (SSR) app, knowledge-navigator │
        │  VITE_API_URL → Render backend                 │
        └───────────────────────────────────────────────┘
                                 │  HTTPS + CORS
                                 ▼
        ┌───────────────────────────────────────────────┐
        │  Backend — Render (FastAPI, "ripplebot-backend")│
        │   • RAG chat, upload, indexing                 │
        │   • folder watcher + startup catch-up indexer  │
        │   • Agentic layer (opt-in, /api/agentic/*)     │
        │   • free tier: NO disk → files rehydrate from  │
        │     Postgres on boot (file_store.py)           │
        └───────────────────────────────────────────────┘
             │                │                     │
             ▼                ▼                     ▼
   ┌──────────────────┐ ┌──────────────────────┐ ┌────────────────────┐
   │ Neon Postgres    │ │ Voyage (embed/rerank)│ │ Claude (Anthropic) │
   │  + pgvector      │ │ Groq / Gemini (LLM)  │ │  agentic brain     │
   │  embeddings/chunks│ └──────────────────────┘ │ Document360 (help) │
   └──────────────────┘                           └────────────────────┘
```

**Tenancy:** every request is scoped by an `X-Company-Id` header. Isolation:
- **pgvector** — shared `documents`/`chunks` tables with a `company_id` column; every query is filtered by it.
- **SQL router (structured tables)** — a separate SQLite file per company at `knowledge_base/db/<company_id>_tables.db`.
- **Uploads** — stored under `knowledge_base/<company_id>/`.
New tenants auto-provision the first time the backend sees a new company id.

**Vector backend is a config switch** (`VECTOR_BACKEND`): `chroma` (embedded, local dev default) or `pgvector` (cloud — Render uses this, pointed at Neon).

**Agentic layer (PIA unification — PRD §18):** an opt-in Claude tool-use loop at
`/api/agentic/*`, **off by default** (`AGENTIC_MODE`). It never touches the existing
`/api/chat/query` pipeline. See §13.

---

## 2. Repository layout (important gotcha)

There are **two GitHub repos** and it matters which you deploy from:

| Repo | What it is | Use for |
|------|-----------|---------|
| **`shobhitrh/RippleBot`** | The full app: backend (`backend/`, `rag_migration_kit/`) **and** the frontend (`knowledge-navigator/` subfolder). This is the source of truth. | Render (backend) **and** Vercel (frontend, Root Directory = `knowledge-navigator`). |
| `shobhitrh/knowledge-navigator` | An older Lovable-managed repo of just the frontend. **Stale** — does not contain the multi-tenant work. | Ignore for deployment. |

> Lesson: deploy the frontend from **`RippleBot`** with **Root Directory `knowledge-navigator`**, not the standalone repo.

---

## 3. Environment variables

### Backend (Render service `ripplebot-backend`)

Set these under the service → **Environment**. The ones declared in `render.yaml`
with `sync: false` must be filled in the dashboard (they're secrets, never in git).

| Variable | Value | Notes |
|----------|-------|-------|
| `PYTHON_VERSION` | `3.11.0` | pinned in `render.yaml` |
| `VECTOR_BACKEND` | `pgvector` | `chroma` for local dev |
| `POSTGRES_URI` | Neon URI (`postgresql://user:pass@ep-xxx.<region>.aws.neon.tech/db?sslmode=require`) | secret; `DATABASE_URL` is read as a fallback |
| `PGVECTOR_ANN_INDEX` | *(unset)* | `none` (default) = exact cosine search. Leave off unless you have a large disk; HNSW is disk-hungry (see §7 #10) |
| `VOYAGE_API_KEY2` | `<key>` | embeddings + reranking (required) |
| `GROQ_API_KEY` (+ `GROQ_API_KEY2`…`_10`) | `<keys>` | primary LLM + fallback pool |
| `GEMINI_API_KEY` | `<key>` | final LLM fallback |
| `CORS_ORIGINS` | `https://<your-app>.vercel.app` | your Vercel domain, comma-separated for multiple; no trailing slash |
| `EMBED_DIM` | *(unset)* | auto-detected (voyage-4-large = 1024); set only to override |
| `FIREFLIES_API_KEY` | `<key>` | Fireflies transcripts. **Use a workspace-admin key** for meetings you weren't in (§11) |
| `FIREFLIES_WEBHOOK_SECRET` | `<secret>` | webhook auth (`?token=` or Fireflies HMAC) |
| `DEFAULT_COMPANY_ID` | *(unset)* | defaults to `default` |
| **Agentic layer (§13) — all optional; off/stub until set** | | |
| `AGENTIC_MODE` | `on` | enables `/api/agentic/query`. **Off by default** |
| `ANTHROPIC_API_KEY` | `<key>` | the Claude orchestration brain |
| `ANTHROPIC_MODEL` | *(unset)* | defaults to `claude-sonnet-5` |
| `ANTHROPIC_CLASSIFIER_MODEL` | *(unset)* | defaults to `claude-haiku-4-5` |
| `DOCUMENT360_API_KEY` | `<key>` | help-center source; run the ingest (§13) |
| `DOCUMENT360_PUBLIC_BASE_URL` | *(optional)* | for article citation links |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | *(optional)* | **read-only** live-DB tool. Must be a DB Render can reach (not a private VPN-only IP) |
| `SUPABASE_URL` / `SUPABASE_JWT_SECRET` | *(optional)* | tenant-scoped auth (falls back to `X-Company-Id`) |

### Frontend (Vercel)

| Variable | Value |
|----------|-------|
| `VITE_API_URL` | `https://ripplebot-backend.onrender.com` (your Render API domain, no trailing slash) |

> `VITE_*` values are **baked in at build time**. Change it → **redeploy** the frontend.

### LLM fallback order
`GROQ_API_KEY` → `GROQ_API_KEY2` → … → `GEMINI_API_KEY`. Each key is tried in turn; a key that errors *before* streaming any token is skipped to the next; only after all Groq keys fail does it try Gemini. (The agentic layer uses Claude for orchestration; Groq/Gemini remain for the standard pipeline and cheap in-tool synthesis.)

---

## 4. Deploy the BACKEND on Render

1. **Create the Neon Postgres first** (managed pgvector — see §12 for why Neon):
   - Create a Neon project; copy the connection string. Enable pgvector is automatic (`CREATE EXTENSION vector` runs on boot), and Neon supports it natively.
   - **Co-locate regions:** Render service in **Singapore** ↔ Neon in **AWS `ap-southeast-1`** (the heavy traffic is web↔DB).
2. **Deploy the API from GitHub:** Render → **New → Web Service → connect `shobhitrh/RippleBot`**. Render reads `render.yaml`:
   - `buildCommand: pip install -r requirements.txt`
   - `startCommand: uvicorn backend.src.main:app --host 0.0.0.0 --port $PORT`
   - `healthCheckPath: /api/health`, `region: singapore`
3. **Set env vars** (see §3). The `sync: false` ones (`POSTGRES_URI`, keys) must be entered in the dashboard.
4. **Persistence (important — differs from Railway):**
   - **Free tier has NO disk** and wipes the filesystem on every deploy AND every ~15-min idle spin-down. RippleBot handles this: **source files + `companies.json` are persisted in Postgres** (`file_store.py`) and rehydrated to a scratch dir on boot; the **startup catch-up indexer** re-indexes them. Embeddings in pgvector (Neon) survive regardless.
   - **Paid instance:** add a `disk:` block in `render.yaml` mounted at `backend/knowledge_base` to keep uploads + per-tenant SQLite on local disk and skip rehydration.
5. **Verify:** open `https://<service>.onrender.com/api/health`. Expect:
   ```json
   { "api": {"status":"online"}, "vector_db": {"status":"connected","backend":"pgvector"} }
   ```
   Tables `documents`/`chunks` auto-create on first connect.
6. **Cold starts:** Render free tier spins down after ~15 min idle (~30s+ cold start). Upgrade to **Starter ($7/mo)** for always-on, or keep it warm with an uptime ping to `/api/health`.

---

## 5. Deploy the FRONTEND on Vercel

1. **Import** → choose **`RippleBot`**.
2. **Root Directory** → `knowledge-navigator`.
3. **Framework Preset** → Vercel auto-detects **TanStack Start** — accept it. Don't set an Output Directory (Nitro's Vercel preset emits `.vercel/output`, configured in `vite.config.ts`).
4. **Environment Variable** → `VITE_API_URL = https://<your-render-api-domain>`.
5. **Deploy.**
6. **Wire CORS back:** set the Render service's `CORS_ORIGINS` to the Vercel domain (e.g. `https://ripple-bot.vercel.app`) and let it redeploy — otherwise the browser blocks all API calls.

> Note: only the **production** Vercel domain is in CORS. Preview deployments (`ripple-bot-git-*.vercel.app`) won't work unless you add them to `CORS_ORIGINS`.

---

## 6. Ingesting data

There's no shared filesystem, so ingest through the app (this tags each doc with its tenant):
- **From the website:** pick the company in the top bar → Knowledge Base → upload (you can also set **Visibility** — "everyone" or a custom participant list).
- **Via API:**
  ```bash
  curl -X POST https://<api>/api/documents/upload \
    -H "X-Company-Id: pinelabs" \
    -F "file=@Pine_Labs_Handover_Sheet.xlsx" -F "category=Other"
  ```
Poll `GET /api/documents` (same `X-Company-Id`) until `index_status: "indexed"`. Files re-index automatically after redeploys (rehydrated from Postgres, then the startup catch-up indexer runs).

---

## 7. Mistakes we made and how we fixed them (war stories)

These are the real bugs going from localhost → cloud. Most were "works on my machine" traps. (Several are Railway-era; the DB-disk ones — #10 — are fully resolved by using managed Neon, see §12.)

| # | Symptom | Root cause | Fix |
|---|---------|-----------|-----|
| 1 | Every pgvector insert would fail | Schema hardcoded `vector(1536)`, but voyage-4-large returns **1024** dims | Auto-detect embedding dimension at startup (`_detect_embedding_dim`) |
| 2 | Frontend built but was missing modules | `.gitignore` had a bare `lib/` (Python) rule that also matched the frontend's **`src/lib/`** | Anchored the Python ignores to root (`/lib/`) and committed `src/lib/` |
| 3 | Vercel build produced Cloudflare output | TanStack Start's Nitro defaulted to the **cloudflare** target | Set `nitro.preset = "vercel"` in `vite.config.ts` |
| 4 | `vector_db: error`, fast fail | DB unreachable — wrong `POSTGRES_URI` or DB down | Verify `POSTGRES_URI` (Neon, `sslmode=require`); logs show `could not translate host name` / `password authentication failed` |
| 5 | `pgvector unavailable: invalid syntax (line 633)` | A dangling `else:` left `rag_pgvector.py` unparseable — never caught locally because local dev uses `chroma` | Fixed the block; now compile-check **both** engines |
| 6 | `invalid error value specified` during indexing | One psycopg2 connection **shared across threads** — psycopg2 forbids this | **Thread-local connections**, serialize builds |
| 7 | `invalid error value specified` (deterministic) | `df.apply(pd.to_numeric, errors='ignore')` — `errors='ignore'` **removed in pandas 3.x** | Version-proof `coerce_numeric_columns()` |
| 8 | Documents vanished after a redeploy | Container disk is **ephemeral** | **Postgres-backed `file_store`** (source files persisted in DB, rehydrated on boot) + **startup catch-up indexer** |
| 9 | Backend **Out of memory** on multi-file ingest | The index build held all chunks + embeddings + stringified vectors in RAM at once → OOM loop | **Streaming indexer**: one file at a time, embed+insert in 100-chunk slices; failed files marked `index_status='failed'` and skipped |
| 10 | **Postgres crashed**, `No space left on device` | The HNSW vector index (+ WAL bloat) filled a small self-managed DB volume | Made the ANN index **opt-in** (`PGVECTOR_ANN_INDEX`, default `none`); **moved the DB to managed Neon** (storage/compute separated — a full disk can't take the process down, §12) |

**Meta-lessons**
- *"Works on localhost" ≠ works in the cloud.* Killers: dependency-version drift (pandas 3), a different default backend (chroma vs pgvector so a whole module never imported), and platform assumptions (ephemeral disk).
- **Compile/import every module a target env will load**, not just the local-default ones.
- **Log full tracebacks** for background work — a bare `str(e)` cost us round-trips.
- **Pin critical dependency majors** or test against the versions the cloud installs.

---

## 8. Troubleshooting

Read Render logs first: service → **Logs**. Errors include full tracebacks.

| Symptom | Likely cause | Check / fix |
|---------|-------------|-------------|
| `/api/health` → `vector_db: error`, `backend: chroma` | `VECTOR_BACKEND` not set to `pgvector` | Set the variable; redeploy |
| `vector_db: error`, `backend: pgvector`, ~ms latency | Can't reach Neon — wrong `POSTGRES_URI`, DB suspended, or SSL | `POSTGRES_URI` correct with `sslmode=require`? Logs show `could not translate host name` / `password authentication failed` |
| `Your project has exceeded the data transfer quota` | Neon **free-tier** egress cap | Upgrade the Neon plan (Launch) — the quota block clears |
| Logs: `type "vector" does not exist` | pgvector extension not enabled | Neon enables it on `CREATE EXTENSION vector` (runs on boot); confirm the Neon project supports it |
| Site loads, but every API call fails (CORS) | `CORS_ORIGINS` mismatch | Must equal the exact Vercel origin (scheme, no trailing slash); redeploy API |
| Upload succeeds but stays `pending`/`0 chunks` | Indexing crash | Logs (full traceback). Historically pandas/threading (§7 #6, #7) |
| Docs disappear after a redeploy | Free tier has no disk | Expected — they rehydrate from Postgres on boot; wait for the catch-up indexer, or use a paid instance + `disk:` block |
| First request very slow (~30s) then fine | Free-tier idle spin-down cold start | Expected; Starter ($7) for always-on, or ping `/api/health` |
| Assistant toggle answers "not configured" | Agentic keys missing on Render | See §13 — set `AGENTIC_MODE=on` + `ANTHROPIC_API_KEY` (+ `DOCUMENT360_API_KEY` for help center); check `GET /api/agentic/status` |

**Health check reference:** `GET /api/health` (optionally with `X-Company-Id`) returns api status, `vector_db`, watcher status, and the tenant's knowledge_base dir. **Agentic readiness:** `GET /api/agentic/status` reports which agentic keys are set and which tools are ready.

---

## 9. Local development

Defaults to embedded ChromaDB — no database needed:
```bash
# Backend (from repo root). Debian/Ubuntu: use a venv (PEP 668).
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.src.main:app --reload --port 8000       # VECTOR_BACKEND defaults to chroma

# Frontend
cd knowledge-navigator
npm install        # or: bun install
npm run dev        # or: bun run dev
```
To exercise the cloud path locally, set `VECTOR_BACKEND=pgvector` and `POSTGRES_URI` to your Neon URI (your laptop can reach Neon directly). This is also how you run the Document360 ingest into the production store — see §13.

Put API keys in a root `.env` (gitignored). The frontend reads `VITE_API_URL` (defaults to `http://localhost:8000`).

---

## 10. Deploy checklist (TL;DR)

- [ ] Neon project created; `POSTGRES_URI` copied (`sslmode=require`)
- [ ] Render Web Service from `RippleBot`, region Singapore (co-located with Neon)
- [ ] `web` vars: `VECTOR_BACKEND=pgvector`, `POSTGRES_URI`, Voyage/Groq/Gemini keys, `CORS_ORIGINS`
- [ ] `/api/health` → `connected` / `pgvector`
- [ ] Vercel: repo `RippleBot`, Root Directory `knowledge-navigator`, `VITE_API_URL` set
- [ ] `CORS_ORIGINS` = Vercel domain, API redeployed
- [ ] Upload a doc → indexes → query returns a grounded answer → other tenant is isolated
- [ ] *(Agentic, optional)* `AGENTIC_MODE=on` + `ANTHROPIC_API_KEY` (+ `DOCUMENT360_API_KEY`); run the D360 ingest; `GET /api/agentic/status` all green (§13)

---

## 11. Fireflies meeting ingestion

Meetings become RAG-searchable per company. We use Fireflies for the transcript +
its own AI summary/action-items (no LLM keys spent summarizing); we only spend
Voyage (embeddings) + Groq/Gemini (answering questions).

### How it works
```
Fireflies "Meeting Summarized" webhook → { meetingId }  (just a trigger)
      │
      ▼
Backend fetches transcript(id) { sentences + summary } via the Fireflies API
      │
      ▼
Route to a company by attendee EMAIL DOMAIN (companies registry)
      • known domain (e.g. pinelabs.com) → that company
      • no known domain              → DISCARDED (not stored)
      ▼
Save FF_<title>_<date>.md (Fireflies summary + FULL transcript) → embed → chat
```

- **Company routing is by attendee email domain, not meeting content.** The
  registry (`GET/POST /api/companies`, persisted at `knowledge_base/companies.json`
  and mirrored to Postgres) maps domains → companies, e.g. `pinelabs.com → pinelabs`.
- **Unmatched meetings are discarded**, not quarantined. Only meetings whose
  attendees include a **registered client domain** are ingested.
- **Lossless:** the full speaker-labeled transcript is stored + embedded; Fireflies'
  summary sits on top for display.

### Configure in Fireflies
- **Webhook URL:** `https://<api>/api/webhooks/fireflies?token=<FIREFLIES_WEBHOOK_SECRET>`
  (single URL; auto-routes by domain). Explicit override: `/api/webhooks/fireflies/<company_id>`.
- **Event:** *Meeting Summarized*.
- **Auth:** the `?token=` must equal `FIREFLIES_WEBHOOK_SECRET` (or the HMAC "Signing Secret").

### Capturing meetings you weren't invited to (org-wide)
A **personal** Fireflies API key only sees **your own** meetings. To ingest every
meeting across the org:
1. Be a **Fireflies workspace admin** on a plan with **team/workspace webhooks + admin API**.
2. Use a **workspace-admin API key** as `FIREFLIES_API_KEY`.
3. Configure the webhook at the **workspace level** (all meetings).
4. Domain routing keeps only the ones with a client-domain attendee.

Prerequisite: Fireflies must actually be recording those meetings (bot auto-joins). If workspace webhooks aren't on your plan, a fallback is a scheduled job that polls the admin API for recent transcripts.

---

## 12. Postgres is managed Neon (not a self-managed DB volume)

A small self-managed Postgres volume will crash the whole DB when it fills (see §7
#10). RippleBot uses **Neon** — serverless Postgres, native pgvector, storage/compute
separated so a full disk can't take the process down, scales to zero. **No code
change** — the app just reads `POSTGRES_URI`.

### Why Neon
- Native **pgvector** (`CREATE EXTENSION vector`), so the existing engine works unchanged.
- Fully managed & serverless — no WAL/checkpoint bloat crashes.
- Widely understood; easy for anyone to operate.

### Region — co-locate with the Render service
The heavy traffic is **web ↔ DB**, so put Neon in the **same region as the Render
service**. Serving India: Render service in **Singapore** + Neon in **AWS
`ap-southeast-1` (Singapore)**.

### Notes
- **Free-tier egress quota:** Neon Free has a monthly data-transfer cap — a large
  ingest (e.g. embedding hundreds of help-center articles) can exhaust it and the DB
  will reject connections until it resets or you upgrade. **Upgrade to Launch** for
  production ingest.
- **Cold starts:** Neon suspends when idle; first query after a quiet spell resumes
  in ~a second — fine for the app and Fireflies webhooks.
- **Secrets:** keep the Neon URI only in Render env vars, never in git.
- **Still local:** the Tier-C structured tables are per-tenant **SQLite** rebuilt from
  the Excel files (source files persisted in Postgres via `file_store`, so they survive
  restarts). Moving them fully into Postgres is a planned, separately-verified change.

---

## 13. Agentic layer (PIA unification — PRD §18/§19)

An opt-in Claude tool-use loop that adds PIA-style capabilities on top of RippleBot's
retrieval: help-center answers, cross-source reasoning, and (when configured) live
operational-DB queries. **Additive and off by default** — the existing
`/api/chat/query` pipeline is never touched. Full design: PRD §18–19.

### Endpoints
- `GET  /api/agentic/status` — reports which keys are set and which tools are ready. Safe to call anytime.
- `POST /api/agentic/query` — the agentic engine (SSE, same contract as `/api/chat/query`). Active only when `AGENTIC_MODE=on`.
- `POST /api/agentic/ingest-help-center` — triggers the Document360 ingest.

### Enable it on Render
Set on the `ripplebot-backend` service:
```
AGENTIC_MODE=on
ANTHROPIC_API_KEY=<key>          # the Claude brain
DOCUMENT360_API_KEY=<key>        # help-center source (optional)
```
Optional model overrides: `ANTHROPIC_MODEL` (default `claude-sonnet-5`),
`ANTHROPIC_CLASSIFIER_MODEL` (default `claude-haiku-4-5`).

### Ingest the Document360 help center
Help articles are embedded **once** into a shared `help_center` store. Because the
ingest is data-transfer heavy, run it from your laptop pointed at Neon (avoids
Render's request timeout on hundreds of articles):
```bash
source .venv/bin/activate
VECTOR_BACKEND=pgvector python -m backend.src.agentic.ingest_d360
```
It retries on Document360 rate limits (429) honouring `Retry-After`, and **resumes**
on re-run (skips already-fetched articles). Then confirm on the deployed app:
```bash
curl https://<service>.onrender.com/api/agentic/status   # search_help_center.ready: true
```
The chat UI shows a **Standard / Assistant** toggle once the engine is live; the
**Assistant** side routes to `/api/agentic/query` (help center + cross-source, Claude-powered).

### Live operational DB tool (optional)
`query_live_database` / `get_tenant_configs` reach a **live** MySQL for real-time data.
Requirements:
- A **read-only** DB user (SELECT-only; enforced at the SQL guard AND the DB grant).
- `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME` set.
- The DB must be **reachable from Render** — a private VPN-only staging IP won't work
  from Render (use it locally with the VPN, or point at a Render-reachable DB in prod).

Until these are set, the live-DB tools return a clear "not configured" message and
the rest of the agentic engine works normally.

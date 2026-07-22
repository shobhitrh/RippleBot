# RippleBot — Deployment & Operations Guide

Everything needed to run RippleBot in the cloud: the architecture, how we use
Railway + Vercel, exact deploy steps, the mistakes we hit and how we fixed them,
environment variables, and troubleshooting.

---

## 1. Architecture at a glance

```
                Browser (users pick a Company in the top bar)
                                 │  every request carries  X-Company-Id
                                 ▼
        ┌───────────────────────────────────────────────┐
        │  Frontend — Vercel                             │
        │  TanStack Start (SSR) app, knowledge-navigator │
        │  VITE_API_URL → Railway backend                │
        └───────────────────────────────────────────────┘
                                 │  HTTPS + CORS
                                 ▼
        ┌───────────────────────────────────────────────┐
        │  Backend — Railway (FastAPI, service "web")    │
        │   • RAG chat, upload, indexing                 │
        │   • folder watcher + startup catch-up indexer  │
        │   • Volume mounted at knowledge_base (files +  │
        │     per-tenant SQLite tables)                  │
        └───────────────────────────────────────────────┘
             │                              │
             ▼                              ▼
   ┌────────────────────┐        ┌──────────────────────────┐
   │ Postgres + pgvector│        │ Voyage (embeddings/rerank)│
   │ (Railway, same     │        │ Groq / Gemini (LLM)       │
   │  project, volume)  │        └──────────────────────────┘
   │  embeddings/chunks │
   └────────────────────┘
```

**Tenancy:** every request is scoped by an `X-Company-Id` header. Isolation:
- **pgvector** — shared `documents`/`chunks` tables with a `company_id` column; every query is filtered by it.
- **SQL router (structured tables)** — a separate SQLite file per company at `knowledge_base/db/<company_id>_tables.db`.
- **Uploads** — stored under `knowledge_base/<company_id>/`.
New tenants auto-provision the first time the backend sees a new company id.

**Vector backend is a config switch** (`VECTOR_BACKEND`): `chroma` (embedded, local dev default) or `pgvector` (cloud/Railway).

---

## 2. Repository layout (important gotcha)

There are **two GitHub repos** and it matters which you deploy from:

| Repo | What it is | Use for |
|------|-----------|---------|
| **`shobhitrh/RippleBot`** | The full app: backend (`backend/`, `rag_migration_kit/`) **and** the frontend (`knowledge-navigator/` subfolder). This is the source of truth. | Railway (backend) **and** Vercel (frontend, Root Directory = `knowledge-navigator`). |
| `shobhitrh/knowledge-navigator` | An older Lovable-managed repo of just the frontend. **Stale** — does not contain the multi-tenant work. | Ignore for deployment. |

> Lesson: deploy the frontend from **`RippleBot`** with **Root Directory `knowledge-navigator`**, not the standalone repo.

---

## 3. Environment variables

### Backend (Railway `web` service)
| Variable | Value | Notes |
|----------|-------|-------|
| `VECTOR_BACKEND` | `pgvector` | `chroma` for local dev |
| `POSTGRES_URI` | `${{Postgres.DATABASE_URL}}` *or* a Neon URI | Railway reference (same-project Postgres) **or** an external managed Postgres like Neon — see §12 |
| `PGVECTOR_ANN_INDEX` | *(unset)* | `none` (default) = exact cosine search, no HNSW index. HNSW is disk-hungry and filled the small Railway volume; leave off on small tiers. Set `hnsw` (or `ivfflat`) only on a larger volume. |
| `VOYAGE_API_KEY2` | `<key>` | embeddings + reranking (required) |
| `GROQ_API_KEY` | `<key>` | primary LLM |
| `GROQ_API_KEY2`, `GROQ_API_KEY3` | `<keys>` | fallback LLM keys (optional) |
| `GEMINI_API_KEY` | `<key>` | final LLM fallback |
| `CORS_ORIGINS` | `https://ripple-bot.vercel.app` | your Vercel domain, comma-separated for multiple; no trailing slash |
| `EMBED_DIM` | *(unset)* | auto-detected (voyage-4-large = 1024); set only to override |
| `FIREFLIES_API_KEY` | `<key>` | Fireflies API key used to fetch transcripts. **Use a workspace-admin key** to access meetings you weren't in (see §11). |
| `FIREFLIES_WEBHOOK_SECRET` | `<secret>` | Shared secret for the webhook. Sent as `?token=` on the webhook URL (or Fireflies HMAC "Signing Secret"). |
| `DEFAULT_COMPANY_ID` | *(unset)* | defaults to `default` |

`DATABASE_URL` is read as a fallback if `POSTGRES_URI` is unset — but set `POSTGRES_URI` explicitly to the reference.

### Frontend (Vercel)
| Variable | Value |
|----------|-------|
| `VITE_API_URL` | `https://web-production-ac577.up.railway.app` (your Railway API domain, no trailing slash) |

> `VITE_*` values are **baked in at build time**. Change it → **redeploy** the frontend.

### LLM fallback order
`GROQ_API_KEY` → `GROQ_API_KEY2` → `GROQ_API_KEY3` → `GEMINI_API_KEY`. Each key is tried in turn; a key that errors *before* streaming any token is skipped to the next; only after all Groq keys fail does it try Gemini.

---

## 4. Deploy the BACKEND on Railway

1. **Create the Postgres database first, in the project you'll deploy the API into.**
   - Project → `+ New` → **Database → Add PostgreSQL** (or the **pgvector** template if listed).
   - ⚠️ **The Postgres and the `web` service MUST be in the same Railway project** — the `${{Postgres.DATABASE_URL}}` reference and private networking only work within one project.
2. **Enable pgvector** (skip if you used the pgvector template): Postgres → **Data** tab → run
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
   The backend also runs this on startup; doing it here confirms the image supports it.
3. **Deploy the API from GitHub:** project → `+ New` → **GitHub Repo → `shobhitrh/RippleBot`**.
   Railway auto-detects Python via the root `requirements.txt` and uses `railway.json`'s start command:
   `uvicorn backend.src.main:app --host 0.0.0.0 --port $PORT`.
4. **Set variables** on the `web` service (see §3). `POSTGRES_URI = ${{Postgres.DATABASE_URL}}`.
5. **Add a Volume** (persist uploads + per-tenant SQLite across redeploys):
   - Canvas → right-click `web` → **Attach Volume** (or `Cmd/Ctrl+K` → "volume").
   - **Mount path: `/app/backend/knowledge_base`**.
   - Without it, uploaded files and the SQL-router tables are wiped on every redeploy (embeddings in pgvector survive regardless).
6. **Generate a public domain:** `web` → **Settings → Networking → Generate Domain**.
7. **Verify:** open `https://<api-domain>/api/health`. Expect:
   ```json
   { "api": {"status":"online"}, "vector_db": {"status":"connected","backend":"pgvector"} }
   ```
   Tables `documents`/`chunks` auto-create on first connect (see them in Postgres → Data).
8. **(Optional) App Sleeping** to save trial credit: `web` → Settings → **Serverless / App Sleeping**. Sleeps when idle, auto-wakes on request (~15–30s cold start). Turn OFF before live demos.

---

## 5. Deploy the FRONTEND on Vercel

1. **Import** → choose **`RippleBot`**.
2. **Root Directory** → `knowledge-navigator`.
3. **Framework Preset** → Vercel auto-detects **TanStack Start** — accept it. Don't set an Output Directory (the build emits `.vercel/output` via Nitro's Vercel preset, configured in `vite.config.ts`).
4. **Environment Variable** → `VITE_API_URL = https://<your-railway-api-domain>`.
5. **Deploy.**
6. **Wire CORS back:** set the Railway `web` service's `CORS_ORIGINS` to the Vercel domain (e.g. `https://ripple-bot.vercel.app`) and let it redeploy — otherwise the browser blocks all API calls.

> Note: only the **production** Vercel domain is in CORS. Preview deployments (`ripple-bot-git-*.vercel.app`) won't work unless you add them to `CORS_ORIGINS`.

---

## 6. Ingesting data

There's no shared filesystem, so ingest through the app (this tags each doc with its tenant):
- **From the website:** pick the company in the top bar → Knowledge Base → upload.
- **Via API:**
  ```bash
  curl -X POST https://<api>/api/documents/upload \
    -H "X-Company-Id: pinelabs" \
    -F "file=@Pine_Labs_Handover_Sheet.xlsx" -F "category=Other"
  ```
Poll `GET /api/documents` (same `X-Company-Id`) until `index_status: "indexed"`. With the volume + startup catch-up indexer, files re-index automatically after redeploys.

---

## 7. Mistakes we made and how we fixed them (war stories)

These are the real bugs we hit going from localhost → cloud. Most were "works on my machine" traps.

| # | Symptom | Root cause | Fix |
|---|---------|-----------|-----|
| 1 | Every pgvector insert would fail | Schema hardcoded `vector(1536)`, but voyage-4-large returns **1024** dims | Auto-detect embedding dimension at startup (`_detect_embedding_dim`) |
| 2 | Frontend built but was missing modules | `.gitignore` had a bare `lib/` (Python) rule that also matched the frontend's **`src/lib/`** — so `utils.ts`, `api.ts`, etc. were never committed | Anchored the Python ignores to root (`/lib/`) and committed `src/lib/` |
| 3 | Vercel build produced Cloudflare output | TanStack Start's Nitro defaulted to the **cloudflare** target | Set `nitro.preset = "vercel"` in `vite.config.ts` |
| 4 | `vector_db: error`, fast fail | Postgres was in a **different Railway project** than the API — `${{Postgres.DATABASE_URL}}` / private networking don't cross projects | Put Postgres in the same project as `web` |
| 5 | `pgvector unavailable: invalid syntax (line 633)` | A dangling `else:` left `rag_pgvector.py` unparseable — never caught locally because local dev uses `chroma`, so that module was never imported | Fixed the block; now compile-check **both** engines |
| 6 | `invalid error value specified` during indexing (intermittent) | One psycopg2 connection **shared across threads** (event loop + watcher timer threads + chat threadpool) — psycopg2 forbids this | **Thread-local connections** (each thread its own), serialize builds |
| 7 | `invalid error value specified` (again, deterministic) | `df.apply(pd.to_numeric, errors='ignore')` — `errors='ignore'` was **removed in pandas 3.x** (cloud) while local pandas 2.x only warned | Version-proof `coerce_numeric_columns()` helper |
| 8 | Documents vanished / re-upload needed after redeploy | Railway container disk is **ephemeral**; uploads + SQLite tables lived there | Persistent **Volume** at `knowledge_base` + **startup catch-up indexer** |
| 9 | `web` service **Out of memory**, multi-file ingest "kept processing" forever | The index build held **all chunks + all embeddings + all stringified vectors** for every new file in RAM at once → OOM-kill → restart → catch-up reindex → OOM loop | **Streaming indexer**: process one file at a time, embed+insert in 100-chunk slices, free each slice. Failed files marked `index_status='failed'` and skipped so one bad file can't OOM-loop |
| 10 | **Postgres crashed**, all features down; `No space left on device` on startup | The **HNSW vector index** (+ WAL bloat from the OOM loop) filled the small 500 MB Railway volume to 100%; Postgres couldn't even write startup WAL → crash loop | Made the ANN index **opt-in** (`PGVECTOR_ANN_INDEX`, default `none` = exact search) and **auto-drop** any leftover HNSW on startup to reclaim disk. Grow the volume (or move to Neon, §12) if it won't boot |

**Meta-lessons**
- *"Works on localhost" ≠ works in the cloud.* The killers were dependency-version drift (pandas 3), a different default backend (chroma vs pgvector so a whole module never imported), and platform assumptions (ephemeral disk, cross-project networking).
- **Compile/import every module a target env will load**, not just the ones the local default uses.
- **Log full tracebacks** for background work — a bare `str(e)` like "invalid error value specified" cost us multiple round-trips. The watcher now logs `exc_info=True`.
- **Pin critical dependency majors** (e.g. `pandas>=2,<3`) or test against the versions the cloud will install.

---

## 8. Troubleshooting

Read Railway logs first: `web` → **Deployments → View logs** (Deploy Logs). Errors now include full tracebacks.

| Symptom | Likely cause | Check / fix |
|---------|-------------|-------------|
| `/api/health` → `vector_db: error`, `backend: chroma` | `VECTOR_BACKEND` not set to `pgvector` | Set the variable; redeploy |
| `vector_db: error`, `backend: pgvector`, ~ms latency | Can't reach DB — cross-project, wrong `POSTGRES_URI`, or DB down | Same project? `POSTGRES_URI = ${{Postgres.DATABASE_URL}}`? Logs show `could not translate host name` / `password authentication failed` |
| Logs: `type "vector" does not exist` | pgvector extension not enabled | Run `CREATE EXTENSION IF NOT EXISTS vector;` or use the pgvector image |
| Site loads, but every API call fails in browser console (CORS) | `CORS_ORIGINS` mismatch | Must equal the exact Vercel origin (scheme, no trailing slash); redeploy API after changing |
| Upload succeeds but stays `pending`/`0 chunks`, logs show an error | Indexing crash | Logs (full traceback). Historically pandas/threading (§7 #6, #7) |
| Docs disappear after a redeploy | No volume | Attach volume at `/app/backend/knowledge_base` |
| First request very slow (~15–30s) then fine | App Sleeping cold start | Expected; disable App Sleeping for demos |

**Health check reference:** `GET /api/health` (optionally with `X-Company-Id`) returns api status, `vector_db` (status/backend/doc_count/chunk_count), watcher status, and the tenant's knowledge_base dir.

---

## 9. Local development

Defaults to embedded ChromaDB — no database needed:
```bash
# Backend (from repo root)
pip install -r backend/requirements.txt
uvicorn backend.src.main:app --reload --port 8000       # VECTOR_BACKEND defaults to chroma

# Frontend
cd knowledge-navigator
npm install
npm run dev
```
To exercise the cloud path locally, set `VECTOR_BACKEND=pgvector` and `POSTGRES_URI` to a local Postgres+pgvector.

Put API keys in a root `.env` or `backend/.env` (both are gitignored). The frontend reads `VITE_API_URL` (defaults to `http://localhost:8000`).

---

## 10. Deploy checklist (TL;DR)

- [ ] Postgres + `web` in the **same** Railway project
- [ ] `CREATE EXTENSION vector;` succeeds
- [ ] `web` vars: `VECTOR_BACKEND=pgvector`, `POSTGRES_URI=${{Postgres.DATABASE_URL}}`, Voyage/Groq/Gemini keys, `CORS_ORIGINS`
- [ ] Volume at `/app/backend/knowledge_base`
- [ ] `/api/health` → `connected` / `pgvector`
- [ ] Vercel: repo `RippleBot`, Root Directory `knowledge-navigator`, `VITE_API_URL` set
- [ ] `CORS_ORIGINS` = Vercel domain, API redeployed
- [ ] Upload a doc → indexes → query returns a grounded answer → other tenant is isolated

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
      • no known domain              → DISCARDED (not stored) ← see below
      ▼
Save FF_<title>_<date>.md (Fireflies summary + FULL transcript) → embed → chat
```

- **Company routing is by attendee email domain, not meeting content.** The
  registry (`GET/POST /api/companies`, persisted at `knowledge_base/companies.json`)
  maps domains → companies, e.g. `pinelabs.com → pinelabs`. Add clients via the
  UI "Add company" (name + domain) or the API.
- **Unmatched meetings are discarded**, not quarantined. The workspace has many
  internal/scrum calls; storing/reviewing them is noise. Only meetings whose
  attendees include a **registered client domain** are ingested. (Design choice —
  changed from an "unassigned" review inbox.)
- **Lossless:** the full speaker-labeled transcript is stored + embedded, so the
  chat can answer anything discussed — Fireflies' summary sits on top for display.

### Configure in Fireflies
- **Webhook URL:** `https://<api>/api/webhooks/fireflies?token=<FIREFLIES_WEBHOOK_SECRET>`
  (single URL; auto-routes by domain). Explicit override: `/api/webhooks/fireflies/<company_id>`.
- **Event:** *Meeting Summarized* (so both transcript AND Fireflies summary are ready).
- **Auth:** the `?token=` must equal `FIREFLIES_WEBHOOK_SECRET` (or use the HMAC
  "Signing Secret" field with the same value).

### Ways meetings get in
1. **Live (automatic):** the webhook → domain routing.
2. **Past/manual:** Meeting Logs → **Import by ID** (transcript id) → pins to the
   selected company. Endpoint: `POST /api/documents/import-fireflies` (X-Company-Id header).
3. **Reassign:** the **Move to…** dropdown on a meeting card → `POST /api/documents/{file}/assign`.

### Capturing meetings you weren't invited to (org-wide) — IMPORTANT
A **personal** Fireflies API key only sees **your own** meetings. To ingest every
Pine Labs meeting across a 100+ person org (including ones you're not in):

1. Be a **Fireflies workspace admin** on a plan that supports **team/workspace
   webhooks + admin API** (Business/Enterprise).
2. Use a **workspace-admin API key** as `FIREFLIES_API_KEY` — only an admin key can
   fetch transcripts of meetings you didn't attend.
3. Configure the webhook at the **workspace level** ("all meetings in the
   workspace", not just yours), so every member's meeting fires it.
4. Domain routing then keeps only the ones with a `@pinelabs.com` attendee and
   discards the rest — fully automatic, no manual review.

Prerequisite: Fireflies must actually be recording those meetings (the org has it
deployed team-wide / bot auto-joins). If workspace webhooks aren't on your plan, a
fallback is a scheduled job that polls the admin API for recent workspace
transcripts and ingests any with a client domain.

---

## 12. Using an external managed Postgres (Neon) instead of Railway's DB

Railway's small-tier Postgres volume is only ~500 MB and, being a self-managed
volume, will crash the whole DB when it fills (see §7 #10). Moving the database to
a managed provider (**Neon** recommended — serverless Postgres, native pgvector,
storage/compute separated so a full disk can't take the process down, scales to
zero) removes that failure mode. **No code change** — the app just reads
`POSTGRES_URI`.

### Why Neon
- Native **pgvector** (`CREATE EXTENSION vector`), so the existing pgvector engine
  works unchanged.
- Fully managed & serverless — nothing stored on the Railway box; WAL/checkpoints
  handled for you (no bloat crashes).
- Postgres = the most widely understood DB, easy for anyone to operate.

### Region — co-locate with the `web` service, not with users
The heavy traffic is **web ↔ DB** (many queries per request), so the DB must sit in
the **same region as the Railway `web` service**. Pick the DB region to match
Railway's region:
- **Serving India:** move the Railway `web` service to **Singapore** (Settings →
  Region) and create the Neon project in **AWS `ap-southeast-1` (Singapore)** — the
  closest region both platforms share.
- **Leaving Railway in the US:** put Neon in that same US region (e.g. `us-east-1`).

### Steps (config only)
1. Create a **Neon** project; copy the connection string
   (`postgresql://user:pass@ep-xxx.<region>.aws.neon.tech/dbname?sslmode=require`).
2. Railway → `web` service → **Variables**: set **`POSTGRES_URI`** to that string.
   (`POSTGRES_URI` takes precedence over Railway's `DATABASE_URL`.) Keep
   `VECTOR_BACKEND=pgvector` and `PGVECTOR_ANN_INDEX` **unset** (exact search, no
   disk-hungry HNSW).
3. **Redeploy** `web`. On boot it connects to Neon, runs `CREATE EXTENSION vector`,
   creates the tables, and the **startup catch-up indexer re-embeds all files from
   the `web-volume`** into Neon (no `pg_dump` needed — the files on the volume are
   the source of truth).
4. Once chat/knowledge/meetings work, **delete the Railway Postgres service + its
   volume** to stop it counting against usage.

### Notes
- **Re-indexing cost:** step 3 re-runs Voyage embeddings for all files once. Modest
  cost; won't OOM thanks to the streaming indexer (§7 #9).
- **Cold starts:** Neon suspends when idle; first query after a quiet spell resumes
  in ~a second — fine for the app and Fireflies webhooks.
- **Secrets:** keep the Neon URI only in Railway Variables, never in git.
- **Still local:** the quantitative-query (Tier-C) tables are per-tenant **SQLite on
  the `web-volume`**. They're persistent and rebuilt from the Excel files, so they
  survive restarts — but they are not yet in Postgres. Moving them into the managed
  Postgres (fully stateless container) is a planned, separately-verified change.

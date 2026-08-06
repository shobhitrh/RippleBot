# RippleBot — Project Guide & Handoff (RippleBot × PIA)

This document is the single source of truth for what RippleBot is now, how it's built,
how to run/deploy it, and — importantly — **the exact action items the DevOps team must
do that we cannot** (VM, network, HTTPS). Read §7 (Deployment) and §8 (DevOps action
items) for the VM setup.

> **TL;DR of current state:** the app is code-complete and validated locally. All code
> + CI/CD is done and pushed to `main`. What remains is **infrastructure only** — a VM
> from DevOps (§8). Everything is additive and flag-gated, so nothing here disrupts the
> original RippleBot.

---

## 1. What this project is

RippleBot is a multi-tenant B2B RAG platform. We merged it with **PIA** (RippleHire's
internal Product Intelligence Agent) so one product serves two audiences:

- **Customers** — the existing per-company chat over their uploaded documents.
- **Internal (CS/product) teams** — a new cross-tenant assistant that answers "how-to"
  from the help center and pulls **live** operational data across every tenant.

The merge is **additive**: PIA's Claude tool-use loop was transplanted on top of
RippleBot's retrieval, and every knowledge source is exposed as a callable tool. The
original `/api/chat/query` pipeline is untouched. Full design rationale: `RippleBot_PRD.md`
§18 (unification architecture) and §19 (prerequisites).

---

## 2. Architecture

Two distinct chat surfaces:

| Surface (UI tab) | Scope | Audience | Backend |
|---|---|---|---|
| **Chatbot Assistant** | one company (selector) | customer-facing | existing RAG pipeline `/api/chat/query` (Groq/Gemini) |
| **Assistant (PIA)** | cross-tenant | internal | agentic loop `/api/agentic/query` (Claude) |

**The agentic layer** (`backend/src/agentic/`) is a Claude tool-use loop with a router:
- Pure how-to questions → **help-center RAG fast path** (`help_rag.py`) — one cheap
  Haiku call over the embedded Document360 articles. No tool loop.
- Live/config/cross-source questions → **full agentic loop** (`loop.py`) with tools.

**The tools** (`tools.py` — one owner each, no overlap):
- RippleBot: `search_knowledge_base`, `exact_cell_lookup`, `query_uploaded_data`, `get_document_section`
- PIA (live): `query_live_database`, `get_tenant_configs`, `list_live_schemas` — **read-only**, audited, **fan out across all tenant schemas**
- Help center: `search_help_center` (Voyage-embedded D360 articles)

**Live-DB topology note:** each tenant spans several module schemas — `<tenant>_buddyto`
(referral), `<tenant>_do` (offers, e-sign), `<tenant>_cp` (candidate portal). The
tenant-name column is `COMPANY_NAME` in base schemas and `DISPLAY_NAME` in `_cp`/`portal`
schemas; `get_tenant_configs` detects this per schema.

---

## 3. Repository structure (key paths)

```
backend/src/
  agentic/            # THE MERGE: Claude loop + tools + help RAG + D360 ingest
    loop.py           #   agentic tool-use loop (Claude), system prompt
    tools.py          #   tool registry + dispatch (no-overlap contract)
    rippletools.py    #   RippleBot retrieval tools (wrap existing functions)
    piatools.py       #   live-DB fan-out + get_tenant_configs + help center
    help_rag.py       #   help-center RAG fast path (cheap, no tool loop)
    classifier.py     #   route: help vs agentic
    router.py         #   /api/agentic/{status,query,ingest-help-center}
    ingest_d360.py    #   pull D360 → embed into shared help_center store
  auth/               # employee Google OAuth + domain gate (off by default)
    router.py service.py deps.py jwt_utils.py
  config.py           # ALL env config + feature flags
  main.py             # FastAPI app; mounts agentic + auth routers defensively
  router/             # existing chat/document/company/webhook (unchanged)
knowledge-navigator/  # frontend (React + TanStack Start, SSR)
  src/routes/assistant.tsx     # the new cross-tenant Assistant (PIA) tab
  src/components/auth-gate.tsx  # "Sign in with Google" gate
  src/lib/{agentic,auth,api}.ts
Dockerfile            # one image: SSR frontend + backend + nginx + supervisord
docker-compose.yml    # single `app` service + data volume (pgvector variant commented)
deploy/
  setup-vm.sh              # one-command VM bootstrap (Docker + nginx + dirs)
  nginx-vm-ripplebot.conf  # VM reverse-proxy block (subdomain → :8000)
  nginx-container.conf     # in-container routing (/api→backend, /→frontend)
  supervisord.conf         # runs backend + frontend + nginx in the container
.github/workflows/deploy-vm.yml  # build → GHCR → SSH deploy (manual-only for now)
.env.vm.example       # env template for the VM
DEPLOY_VM.md          # deployment runbook
RippleBot_PRD.md      # §18 unification, §19 prerequisites
```

---

## 4. Configuration — environment variables

All config is in `.env` (gitignored). Feature flags default OFF so the app behaves like
the original until you opt in. Template: `.env.vm.example`.

**Core (existing):** `VECTOR_BACKEND` (chroma|pgvector), `POSTGRES_URI`, `VOYAGE_API_KEY2`,
`GROQ_API_KEY`(+`2..10`), `GEMINI_API_KEY`, `FIREFLIES_API_KEY`, `CORS_ORIGINS`.

**Agentic layer (PIA):**
| Var | Purpose |
|---|---|
| `AGENTIC_MODE` | `on` enables `/api/agentic/query` (off = original app only) |
| `ANTHROPIC_API_KEY` | the Claude orchestration brain |
| `ANTHROPIC_MODEL` / `ANTHROPIC_CLASSIFIER_MODEL` | default `claude-sonnet-5` / `claude-haiku-4-5` |
| `DOCUMENT360_API_KEY` | help-center source (then run the ingest) |
| `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD` | **read-only** live MySQL (fan-out discovers schemas; `DB_NAME` optional) |
| `LIVE_DB_SCHEMAS` | optional allowlist to bound the fan-out |

**Employee auth (off by default):**
| Var | Purpose |
|---|---|
| `AUTH_ENABLED` | `on` gates the app behind Google sign-in |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth client |
| `ALLOWED_DOMAIN` | e.g. `ripplehire.com` — only these accounts pass |
| `GOOGLE_REDIRECT_URI` | must match the Google console (`…/api/auth/callback`) |
| `FRONTEND_URL` | where to send the user (with token) after login |
| `JWT_SECRET_KEY` | signing key — set a real `openssl rand -hex 32` for prod |

**Endpoints to verify config:** `GET /api/agentic/status`, `GET /api/auth/status`,
`GET /api/health`.

---

## 5. Local development

```bash
# Backend (RHEL/Debian: use a venv — PEP 668)
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.src.main:app --reload --port 8000   # VECTOR_BACKEND defaults to chroma

# Frontend
cd knowledge-navigator && bun install && bun run dev
```
Live-DB questions require the staging VPN up (`vpn-shuttle-staging up`; if it fails with an
auth error, run `gcloud auth login` first). Help center answers require the D360 ingest to
have run (`python -m backend.src.agentic.ingest_d360`).

---

## 6. What's been built & validated

- **Agentic engine** (Claude) — help center + uploaded docs + live-DB, with a cheap
  help-RAG fast path and multi-schema fan-out. Verified on 7 edge cases: anti-hallucination,
  read-only safety, SQL-injection safety, graceful degradation, honesty on misses — all pass.
- **Separate Assistant (PIA) tab** — cross-tenant, decoupled from the company selector.
- **Employee Google-OAuth auth** — domain-gated to `ALLOWED_DOMAIN`, JWT sessions,
  frontend sign-in gate. Off by default.
- **Deployment** — single Docker image, GHCR registry, GitHub Actions → SSH deploy.
- **Safety/compliance** — live DB is **read-only** (SQL guard + read-only DB user + audit
  log `db_audit.log`); secrets only in gitignored `.env`; no writes to non-local DBs.

---

## 7. Deployment (what we own)

**Model:** GitHub Actions builds the image → pushes to **GHCR** (`ghcr.io/shobhitrh/ripplebot`,
using the built-in `GITHUB_TOKEN`) → SSHes into the VM → the VM pulls & runs. The VM's
nginx proxies the subdomain to the container on `:8000`; TLS terminates at a load balancer.
**No GCP project or registry access is needed** (we moved off Artifact Registry to GHCR).

**Container:** one image runs the SSR frontend + FastAPI backend + an internal nginx via
supervisord, exposing port `:8000`. Data (Chroma vectors, uploads, SQLite) lives in a named
Docker volume.

**Registry auth:** CI push = automatic; the VM pulls the private image with a GitHub PAT
(`read:packages`) stored as the `GHCR_PAT` secret.

The deploy workflow is currently **manual-only** (`workflow_dispatch`) — re-enable the
`push: [main]` trigger (2 commented lines in `deploy-vm.yml`) once the VM + secrets exist.

---

## 8. ⚠️ DevOps action items (WE CANNOT DO THESE — needed to go live)

Everything code/CI-side is done. Going live is blocked **only** on infrastructure. DevOps
(Hatim) must provide:

### 8.1 The VM
- **RHEL 9**, **2–4 vCPU / 8 GB RAM / 50 GB SSD**, **dedicated** (not shared with other tools).
- **Inside the `ripplehire-staging-env` VPC** — it MUST reach the staging DB at
  `10.118.0.3:3306` by private IP (the live-DB tool depends on it).
- **Outbound HTTPS (443)** to: `api.anthropic.com`, `api.voyageai.com`, `api.groq.com`,
  `generativelanguage.googleapis.com`, `apihub.document360.io`, `api.fireflies.ai`,
  `github.com`, `ghcr.io`.
- **SSH access** for the deploy — add our deploy **public key** to the VM's
  `~/.ssh/authorized_keys` (we generate the key pair).

### 8.2 Public HTTPS ingress
- A **GCP HTTPS Load Balancer** with a **managed TLS cert** for **`chatbot.ripplehire.com`**,
  forwarding to the VM on **port 80**. (TLS terminates at the LB; the VM stays HTTP
  internally — that's why the VM nginx listens on 80.)
- **DNS** A record for `chatbot.ripplehire.com` → the LB IP.

### 8.3 Optional
- A **GCS bucket** for nightly DB/volume backups (the VM's service account gets write access).

### 8.4 NOT needed (clarifications)
- **No GCP project / Artifact Registry access** — we use GHCR.
- **No changes to infosec-tool or other tools** — RippleBot is a separate VM + subdomain.
- The **read-only staging DB user** (`cu_`-prefixed) is already sorted.

---

## 9. Full VM setup steps (run after DevOps provisions the box)

**A. Prep in parallel (no VM needed):**
1. Create a GitHub **PAT** with `read:packages` → this is the `GHCR_PAT` secret.
2. Generate the SSH deploy key: `ssh-keygen -t ed25519 -f ripplebot_deploy -N ""` — give the
   **public** half to DevOps for the VM; the **private** half becomes the `VM_SSH_KEY` secret.
3. Prepare the `.env` values from `.env.vm.example`.

**B. Bootstrap the VM (one command):**
```bash
# copy the repo (or just deploy/setup-vm.sh) to the VM, then:
bash deploy/setup-vm.sh     # installs Docker + compose + nginx, creates /opt/ripplebot
```

**C. Configure the VM:**
```bash
# reverse proxy (edit server_name to chatbot.ripplehire.com)
sudo cp deploy/nginx-vm-ripplebot.conf /etc/nginx/conf.d/ripplebot.conf
sudo nginx -t && sudo systemctl reload nginx

# secrets: create /opt/ripplebot/.env from .env.vm.example with real values
# let the VM pull the private image:
echo <GHCR_PAT> | docker login ghcr.io -u shobhitrh --password-stdin
```

**D. Set GitHub repo secrets** (Settings → Secrets → Actions):
`VM_HOST`, `VM_USER`, `VM_SSH_KEY`, `GHCR_PAT`.

**E. Deploy:** GitHub → Actions → **Build & Deploy (VM)** → **Run workflow**. It builds →
pushes to GHCR → SSHes in → pulls, `compose up`, verifies image, polls `/api/health`.

**F. Verify & finish:**
```bash
curl -s http://localhost:8000/api/health
curl -s http://localhost:8000/api/agentic/status
# ingest the help center into the VM's store:
docker compose -f /opt/ripplebot/docker-compose.yml exec app python -m backend.src.agentic.ingest_d360
```
Then set `AUTH_ENABLED=on` (+ Google creds) in `/opt/ripplebot/.env` and
`docker compose up -d` to enable employee sign-in. Open `https://chatbot.ripplehire.com`.

---

## 10. Current status & remaining items

**Done (pushed to `main`):** agentic engine, Assistant tab, help-center RAG, live-DB
fan-out + schema fixes, employee auth (backend + frontend), Docker/compose/GHCR CI, VM
scripts, PRD §18/§19. Validated locally end-to-end.

**Remaining — infrastructure only (DevOps, §8):** provision the VM → HTTPS LB + subdomain
→ we run `setup-vm.sh` + set 4 secrets → Run workflow. Then ingest D360 on the VM and flip
`AUTH_ENABLED=on`.

**Later / Phase 2 (not blocking):** customer (external tenant) auth via Supabase; move the
vector store to managed Postgres; integrate the UI into the RippleHire website; cost tuning
on token-heavy fan-out.

---

## 11. Conventions for future changes

- **Everything additive & flag-gated** — never break the original `/api/chat/query` path.
- **Live DB is read-only, always** — SQL guard + read-only user + audit log. Never add write paths.
- **Secrets only in gitignored `.env`** — never commit real keys (`.env.vm.example` stays empty).
- **Commit style (org rule):** every GitHub commit message starts with the Jira ticket id
  (e.g. `PRD-8322 …`) and ends with a `Workflow: rh-assist` trailer on its own line.

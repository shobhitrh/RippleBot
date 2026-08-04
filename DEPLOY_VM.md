# RippleBot — VM Deployment Runbook

Containerized deploy to a GCE VM, mirroring `trampolinetech/infosec-tool`'s pattern.
Frontend + backend ship in **one image**; the VM only pulls & runs.

## Architecture
```
Customers + RippleHire employees
        │  HTTPS
        ▼
[ GCP HTTPS Load Balancer ]  ← TLS terminates here
        │  HTTP
        ▼
[ VM (in ripplehire-staging-env VPC) ]
   nginx (shared)  ──►  container :8000
        │                    ├─ internal nginx :8000
        │                    ├─ FastAPI /api   :8001
        │                    └─ TanStack SSR / :3000   (supervisord)
        │                    volume: /app/data (Chroma + uploads + SQLite)
        └─► reaches Cloud SQL MySQL 10.118.0.3 (live-DB fan-out, read-only)
```

## Auth — employees only (ported from infosec-tool)
Google OAuth 2.0 with a domain gate: only `@ripplehire.com` accounts can sign in
(`ALLOWED_DOMAIN=ripplehire.com`), then a 24h JWT. Same pattern PIA used. Customer
(external, tenant-scoped) access is a later phase; the initial launch is employee-only.

## One-time setup
1. **VM:** RHEL 9 in the VPC, Docker + docker-compose installed, `/opt/ripplebot/` created.
2. **`.env`:** copy `.env.vm.example` → `/opt/ripplebot/.env`, fill in keys (DevOps-managed).
3. **GitHub secrets:** `GCP_SA_KEY` (service-account JSON with Artifact Registry write), `VM_HOST`, `VM_USER`, `VM_SSH_KEY` (deploy key). Set `GCP_PROJECT` + image path in `deploy-vm.yml`. The VM pulls via its own GCP service account (no PAT).
4. **Google OAuth:** create an OAuth client; redirect URI `https://chatbot.ripplehire.com/api/auth/callback`; put id/secret in `.env`.
5. **DNS/LB:** point `chatbot.ripplehire.com` at the HTTPS LB → VM; add the `deploy/nginx-vm-ripplebot.conf` server block to the VM's shared nginx.

## Deploying
- **Automatic:** push to `main` → GitHub Actions builds → pushes to **GCP Artifact Registry** (`asia-south1-docker.pkg.dev/<project>/tools/ripplebot`) → SSHes to the VM → prune, pull, `compose up`, verify image match, poll `/api/health`.
- **Manual:** Actions tab → *Build & Deploy (VM)* → *Run workflow*.

## Files
| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage: build SSR frontend → runtime with Python + Node + nginx + supervisord |
| `deploy/supervisord.conf` | Runs backend + frontend + internal nginx |
| `deploy/nginx-container.conf` | In-container: `/api`→backend, `/`→frontend, on :8000 |
| `docker-compose.yml` | Single `app` service + `ripplebot-data` volume (pgvector variant commented) |
| `.github/workflows/deploy-vm.yml` | Build → GHCR → SSH deploy |
| `deploy/nginx-vm-ripplebot.conf` | VM shared-nginx server block (subdomain → :8000) |
| `.env.vm.example` | Env template for the VM |

## Data & backups
Everything stateful lives in the `ripplebot-data` volume (Chroma vectors, uploaded
files, per-tenant SQLite). Back it up nightly:
```bash
docker run --rm -v ripplebot-data:/data -v /backup:/backup alpine \
  tar czf /backup/ripplebot-$(date +%F).tar.gz -C /data .
# then push /backup to a GCS bucket
```

## Notes / open items
- **Registry:** **GCP Artifact Registry** (matches infosec-tool; the VM authenticates
  via its own GCP service account — no PAT to manage). Set `GCP_PROJECT` and the image
  path in `deploy-vm.yml` to RippleBot's project.
- **Auth:** employee-only via Google OAuth + `ALLOWED_DOMAIN=ripplehire.com`, ported
  from infosec-tool into `backend/src/auth/`. OFF by default (`AUTH_ENABLED`); set it on
  + fill the Google creds in `.env` to enforce. Needs a Google OAuth client (redirect
  `https://<host>/api/auth/callback`).
- **Vector store:** defaults to **embedded Chroma** (simplest, matches infosec-tool).
  Flip to Postgres+pgvector via the commented compose service + `.env`.
- **Live-DB tool** works from the VM (in-VPC, reaches `10.118.0.3`) — unlike Render.
- **First build** will exercise the SSR node-server output; if the frontend build
  needs tweaks, they're isolated to `knowledge-navigator` + the Dockerfile stage 1.

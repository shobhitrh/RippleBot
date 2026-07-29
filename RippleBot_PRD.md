# RippleBot — Product Requirements & Enterprise Scaling Plan

**Document Type:** Product Requirements Document (PRD) + Technical Project Plan  
**Version:** 1.1  
**Date:** July 2026  
**Classification:** Internal — Confidential

---

## ⚡ Executive Quick-Read Summary (Overview of All 12 Sections)

For stakeholders needing a 2-minute overview before diving into the complete document:

| Section | Key Takeaway / Summary |
| :--- | :--- |
| **1. Executive Summary** | RippleBot is a B2B RAG engine serving 35 enterprise client companies (e.g. Pine Labs). Uses a proprietary 3-Stage Excel/CSV pipeline + USIE v4 Dual Engine Router (Cell Index → Text-to-SQL → Vector Reranker). |
| **2. Current State (360° Audit)** | Excellent core IP (Excel unmerging, formula capture, exact cell indexing). **Critical Gap:** Currently zero API auth layer — anyone with the URL can query any tenant. Needs immediate JWT security hardening. |
| **3. Enterprise Roadmap** | **Phase 1:** Security Hardening (JWT, RBAC, Audit Logs). **Phase 2:** Knowledge Version Control & Custom Participant Visibility. **Phase 3:** Multi-source Connectors (SharePoint, GDrive, Confluence) & White-labeling. |
| **4. Access Control & Custom Visibility** | Supabase Auth + JWT. Features **Participant-Level Custom Access Control**: Documents default to "Visible to All", or set to "Custom" with explicit email/user whitelist. Queries filter out unauthorized chunks seamlessly ("No information found" returned to unauthorized users). |
| **5. Knowledge Version Management** | SHA-256 document versioning with 30-day rollback window. Includes **Self-Healing Reindexer** (auto-detects changed files) and **Legacy Chunk Pruner** (auto-deletes superseded document data). |
| **6. Multi-Company Architecture** | 35-company scale using PostgreSQL schema-per-tenant (`tenant_<company_id>`). Total isolation guarantee at JWT, DB schema, and filesystem levels. |
| **7. AI & LLM Strategy** | **Embeddings & Reranker:** Strictly standardized on **Voyage AI `voyage-4-large`** (embeddings) and **`voyage-rerank-2.5`** (semantic reranker). **LLM:** Primary Groq (Llama 3.3 70B, free) + Fallback Gemini 2.0 Flash / Claude Haiku 3.5. |
| **8. Cost Analysis & Budget (35 Companies)** | **Ultra-Low Cost Focus:** Total monthly expense for all 35 companies combined can be as low as **~$15–40/month** total. Vercel remains **100% FREE**; Neon Launch plan is ~$5–12/mo; Voyage AI is ~$10–25/mo; Groq/Gemini LLMs ~$0–3/mo. |
| **9. Infrastructure & Deployment** | Vercel (Frontend, Free) + Render Starter ($7/mo, or Free with keep-alive script) + Neon PostgreSQL + GitHub Actions CI/CD pipeline. |
| **10. Security & Compliance** | Full tenant isolation, prompt injection defense on Text-to-SQL, audit trail, and DPDP Act 2023 readiness for Indian enterprise clients. |
| **11. Implementation Timeline** | **Phase 1:** Weeks 1–10 (Security & Auth). **Phase 2:** Weeks 11–26 (Version Control & Custom Visibility). **Phase 3:** Months 6–12 (Connectors & White-label). |
| **12. Risk Register & Mitigations** | Top risks: Unauthenticated API (mitigated by P0 JWT auth), Rate limits (mitigated by key rotation), DB storage exhaustion (mitigated by Neon paid Launch plan + automated backup). |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current State Architecture (360° Audit)](#2-current-state-architecture)
3. [Enterprise Feature Roadmap](#3-enterprise-feature-roadmap)
4. [Access Control & Custom Participant Visibility System](#4-access-control--custom-participant-visibility-system)
5. [Knowledge Version Management](#5-knowledge-version-management)
6. [Multi-Company Scaling Architecture](#6-multi-company-scaling-architecture)
7. [AI & LLM Strategy (Voyage-4-Large & Rerank-2.5)](#7-ai--llm-strategy-voyage-4-large--rerank-25)
8. [Cost Analysis & Budget Planning (35 Companies Focus)](#8-cost-analysis--budget-planning-35-companies-focus)
9. [Infrastructure & Deployment Architecture](#9-infrastructure--deployment-architecture)
10. [Security & Compliance](#10-security--compliance)
11. [Implementation Phases & Timeline](#11-implementation-phases--timeline)
12. [Risk Register & Mitigations](#12-risk-register--mitigations)

---

## 1. Executive Summary

**RippleBot** is an Enterprise AI Knowledge Base & RAG (Retrieval-Augmented Generation) platform built to serve B2B SaaS clients. It connects to structured data (Excel, CSV), unstructured data (PDF, Word, meeting transcripts from Fireflies), and delivers instant, accurate responses through a 3-stage proprietary pipeline combining Exact-Cell Indexing, Text-to-SQL Analytics, and Voyage AI Semantic Reranking.

The platform is designed to serve **35 active company clients** (e.g. Pine Labs, HDFC). This document defines the complete roadmap to transform the system into a **multi-company enterprise SaaS platform** with a full access control system, document participant visibility rules, knowledge versioning, self-healing AI reindexing, and ultra-lean cost optimization.

### Production Stack

| Layer | Technology | Hosting | Cost Tier |
| :--- | :--- | :--- | :--- |
| Frontend | React + Vite + TanStack Router + Tailwind | Vercel | **100% Free** (Hobby) |
| Backend API | FastAPI + Python 3.11 | Render | Free Tier or Starter ($7/mo) |
| Vector Store | pgvector (PostgreSQL) | Neon | Launch (~$5–12/mo total) |
| Structured Table Store | Tier-C SQLite → PostgreSQL | Neon | Included in DB storage |
| Embeddings | **Voyage AI `voyage-4-large`** | Voyage AI API | Pay-per-use (~$10–18/mo) |
| Semantic Reranker | **Voyage AI `voyage-rerank-2.5`** | Voyage AI API | Pay-per-use (~$5–8/mo) |
| LLM (Primary) | Groq — Llama 3.3 70B | Groq API | **Free** (Key rotation pool) |
| LLM (Fallback) | Google Gemini 2.0 / 1.5 Flash | Gemini API | Free / Near-Zero (~$1–3/mo) |
| Meeting Ingestion | Fireflies.ai GraphQL Webhook | Fireflies API | Shared Pro Account |
| File Persistence | PostgreSQL Binary Store | Neon | Included in DB storage |

---

## 2. Current State Architecture (360° Audit)

### 2.1 What Works Well

**✅ 3-Stage Excel/CSV Pipeline (Differentiating IP)**
- **Stage 1:** Smart cell unmerging, merged-header reconstruction, formula text capture, relational table extraction, and multi-encoding/multi-delimiter CSV detection.
- **Stage 2:** `.md.gz` Gzip archive compression (80–90% storage reduction on disk).
- **Stage 3:** Semantic RAG chunks with Table of Contents, breadcrumbs, and header-injected context for reranker precision.
- Handles real-world "messy" enterprise Excel files correctly.

**✅ USIE v4 Dual-Engine Chat Router**
- **Tier A (Exact Cell Index):** Sub-1.2s deterministic lookup from a persisted `__cell_index__` table.
- **Tier B (Text-to-SQL Analytics):** LLM generates parameterized SQL against the live relational table store. Handles `COUNT`, `SUM`, `AVG`, `GROUP BY` queries on structured data without embedding.
- **Tier C (Semantic Vector + Voyage Reranker):** Semantic fallback for policy documents and transcripts. Voyage `voyage-rerank-2.5` re-scores top candidates.
- Automatic fallback chaining: Tier A → Tier B → Tier C → LLM synthesis.

**✅ Multi-Tenancy (Schema Isolation)**
- Per-tenant PostgreSQL schema: `tenant_<company_id>` — full SQL and vector data isolation.
- Per-tenant file directories: `knowledge_base/<company_id>/`.
- Per-tenant company registry: JSON + PostgreSQL `companies` table.

### 2.2 Current Limitations & Technical Debt

| # | Limitation | Impact | Priority |
| :--- | :--- | :--- | :---: |
| L1 | No authentication layer — any user with backend URL can query any `X-Company-Id` | **Critical** — data breach risk | P0 |
| L2 | No role-based access control (RBAC) — all users see all company documents | High | P0 |
| L3 | No participant-level document visibility rules | High | P1 |
| L4 | No knowledge version management — uploading new doc replaces chunks without tracking history | High | P1 |
| L5 | Render Free Tier spins down on idle (cold-start ~30s latency) | Medium | P1 |
| L6 | No audit log — no record of who queried what | High (compliance) | P1 |
| L7 | No rate limiting on `/api/chat` | Medium | P1 |
| L8 | Webhook delivery has no retry queue | Medium | P2 |

---

## 3. Enterprise Feature Roadmap

### Phase 1 — Production Hardening (Months 1–2)
> **Goal:** Secure the platform and make it 100% compliant for production demo and live client usage.

| Feature | Description | Effort |
| :--- | :--- | :---: |
| **Authentication** | JWT-based login + company-scoped sessions | 2 weeks |
| **RBAC (Roles)** | Owner / Admin / Editor / Viewer per company | 1 week |
| **Rate Limiting** | Token bucket on `/api/chat` per user/company | 3 days |
| **Audit Log** | Log every chat query + document action to DB | 3 days |
| **Re-index UI** | "Refresh Index" button per document in Knowledge dashboard | 2 days |
| **Webhook Retry Queue** | Dead-letter queue for failed Fireflies deliveries | 1 week |
| **Render Keep-Alive / Starter** | Prevent cold starts via ping service or Starter tier ($7/mo) | 1 day |

### Phase 2 — Enterprise Scalability & Custom Visibility (Months 3–5)
> **Goal:** Support 35 client companies with knowledge versioning and granular document visibility rules.

| Feature | Description | Effort |
| :--- | :--- | :---: |
| **Custom Participant Visibility** | Document access control (Visible to All vs. Custom Participant whitelist) | 2 weeks |
| **Knowledge Version Control** | Track document versions; rollback to previous index state | 3 weeks |
| **Self-Healing Reindexer** | Auto-detect stale/changed documents, reindex incrementally | 2 weeks |
| **Legacy Version Pruning** | Auto-delete superseded document versions after grace period | 1 week |
| **Multi-LLM Router** | Per-company LLM selection (Groq/Gemini/Claude/GPT-4o) | 2 weeks |
| **Admin Dashboard** | Company management, user management, usage analytics UI | 4 weeks |
| **Conversation Memory** | Per-user, cross-session chat history | 2 weeks |
| **Streaming Responses** | Server-Sent Events (SSE) for real-time token streaming | 1 week |

### Phase 3 — Intelligent Platform (Months 6–12)
> **Goal:** Multi-source ingestion and enterprise white-labeling.

| Feature | Description | Effort |
| :--- | :--- | :---: |
| **Change Detection Engine** | Monitor for new document versions, auto-diff, prompt admin | 3 weeks |
| **Multi-source Connectors** | SharePoint, Google Drive, Confluence, Notion, S3 ingestion | 6 weeks |
| **White-label Portal** | Per-company custom domain + branding | 3 weeks |
| **Usage-based Billing API** | Track tokens/queries per company for SaaS billing | 2 weeks |
| **SOC 2 Compliance Layer** | Encryption at rest/transit, access logs, data retention policy | 6 weeks |

---

## 4. Access Control & Custom Participant Visibility System

### 4.1 Technical Feasibility Analysis: Custom Participant Visibility

> **Question:** Can document-level / participant-level access restriction be implemented easily?  
> **Answer: YES, VERY EASILY.**  
> By extending the chunk and SQL table metadata with `visibility_type` and `allowed_participants`, vector search and SQL queries simply append a metadata filter matching the requesting user's email/ID. If a user is not authorized, the vector search returns 0 relevant chunks, and the system naturally responds with **"No information found."**

### 4.2 How Custom Participant Visibility Works

#### 1. Ingestion Settings (UI & API)
When uploading any document (Excel, CSV, PDF, DOCX), the uploader chooses:
- **Default Option — `Visible to All`:** `visibility_type = "all"`. Every authorized employee in that company can query data from this document.
- **Custom Option — `Custom Participants Only`:** `visibility_type = "custom"`. The uploader specifies a list of allowed emails or user IDs: `allowed_participants = ["shobhit@pinelabs.com", "finance-lead@pinelabs.com"]`.

#### 2. Database Metadata Schema
In PostgreSQL (`tenant_<company_id>.chunks` and `document_versions`):
```sql
ALTER TABLE document_versions ADD COLUMN visibility_type VARCHAR(20) DEFAULT 'all';
ALTER TABLE document_versions ADD COLUMN allowed_participants TEXT[] DEFAULT '{}';

-- Vector Chunks metadata JSON payload:
-- {
--   "source": "Q3_Executive_Compensation.xlsx",
--   "visibility_type": "custom",
--   "allowed_participants": ["shobhit@pinelabs.com", "cfo@pinelabs.com"]
-- }
```

#### 3. RAG Query Filtering (Zero Information Leakage)
When user `john@pinelabs.com` asks a question:
1. **Tier A / Tier B (SQL Engine):** The generated SQL appends:
   ```sql
   WHERE (visibility_type = 'all' OR 'john@pinelabs.com' = ANY(allowed_participants))
   ```
2. **Tier C (Voyage Reranker & pgvector Search):** The pgvector query applies metadata filtering before reranking:
   ```sql
   SELECT chunk_text, embedding <-> query_vector AS dist
   FROM tenant_pinelabs.chunks
   WHERE (visibility_type = 'all' OR 'john@pinelabs.com' = ANY(allowed_participants))
   ORDER BY dist LIMIT 20;
   ```
3. **Outcome for Unauthorized Users:** If `john@pinelabs.com` queries executive salary data from a restricted document, 0 chunks match the filter. Voyage Reranker receives empty input. The system returns:  
   👉 **"No information found."** (Identical to querying non-existent data, preventing inference attacks).

---

## 5. Knowledge Version Management

### 5.1 The Problem
Currently, uploading a new version of a document overwrites the previous chunks permanently. If an upload contains errors or needs to be audited against last month's data, historical information is lost.

### 5.2 Proposed: Knowledge Version Control System

```sql
CREATE TABLE document_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      VARCHAR(150) NOT NULL,
    filename        VARCHAR(500) NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    content_hash    VARCHAR(64) NOT NULL,    -- SHA-256 of file content
    upload_size     BIGINT,
    uploaded_by     UUID REFERENCES users(id),
    uploaded_at     TIMESTAMP DEFAULT NOW(),
    visibility_type VARCHAR(20) DEFAULT 'all',
    allowed_participants TEXT[] DEFAULT '{}',
    index_status    VARCHAR(50) DEFAULT 'pending',
    is_active       BOOLEAN DEFAULT TRUE,    -- Only 1 active version per filename
    chunk_ids       TEXT[],
    UNIQUE (company_id, filename, version)
);
```

### 5.3 Self-Healing Reindexer & Legacy Version Pruning

- **Self-Healing Reindexer:** Background worker checks physical file content hashes against `document_versions`. Auto-ingests modified files without manual intervention.
- **Legacy Version Pruner:** Old superseded document versions (`is_active = FALSE`) are retained for a 30-day grace period for rollback purposes, after which a scheduled job purges their vector chunks and SQL tables to save database storage.

---

## 6. Multi-Company Scaling Architecture

### Scaling for 35 Enterprise Client Companies

With 35 companies (e.g. Pine Labs, HDFC, etc.):
- **DB Schema:** 1 PostgreSQL database (Neon), 35 schemas (`tenant_company1` to `tenant_company35`).
- **Data Isolation:** Complete separation of SQL tables and pgvector chunks per tenant.
- **Total Estimated Storage for 35 Companies:** ~1.5 GB to 3.5 GB total (extremely small footprint thanks to 3-stage `.md.gz` compression).

---

## 7. AI & LLM Strategy (Voyage-4-Large & Rerank-2.5)

### 7.1 Standardized Embedding & Reranking Stack

Per strict enterprise quality requirements, RippleBot standardizes on Voyage AI's flagship models:

| Function | Model | Dimensions | Price (per 1M tokens) | Rationale |
| :--- | :--- | :---: | :---: | :--- |
| **Vector Embeddings** | **`voyage-4-large`** | 1536 / 1024 | $0.12 / 1M tokens | SOTA retrieval accuracy for complex technical, legal, and tabular content. |
| **Semantic Reranker** | **`voyage-rerank-2.5`** | — | $0.05 / 1M tokens | Best-in-class reranking precision for context-window compression. |

### 7.2 LLM Routing Strategy

| Query Type | Primary Model | Fallback Model | Cost Profile |
| :--- | :--- | :--- | :--- |
| **Exact Cell & SQL Analytics** | Groq (Llama 3.3 70B) | Gemini 2.0 Flash | **$0** (Free key pool) |
| **Document Summarization** | Gemini 2.0 Flash | GPT-4o Mini | ~$0.10 / 1M tokens |
| **Complex Policy Queries** | Claude Haiku 3.5 | Gemini 2.0 Flash | ~$0.80 / 1M tokens |

---

## 8. Cost Analysis & Budget Planning (35 Companies Focus)

### 8.1 Strategy: Maximize Free Tiers & Keep Expenses Ultra-Low

We evaluated whether **Vercel Pro ($20/mo)** or paid backend infrastructure is strictly required for 35 companies:

1. **Vercel Frontend — KEEP 100% FREE ($0/mo):**
   - Vercel's Hobby (Free) tier includes 100 GB bandwidth/month and unlimited static SPA deployments.
   - For 35 client companies using a single React Vite SPA connected to the FastAPI backend, bandwidth will consume < 15 GB/month.
   - **Verdict: $0/month (No need for Vercel Pro).**

2. **Render Backend — $0/mo (Free with Keep-Alive) or $7/mo (Starter):**
   - Render Free tier spins down after 15 mins of inactivity (causing 30s cold start).
   - *Option A ($0/mo):* Setup a free UptimeRobot ping script (`/api/health`) every 10 mins to keep it awake 24/7.
   - *Option B ($7/mo):* Upgrade to Render Starter for guaranteed 24/7 zero-cold-start performance.

3. **Neon PostgreSQL Database — ~$5–12/month (Total for all 35 companies):**
   - 35 companies with compressed `.md.gz` stores consume ~2.0 GB storage total.
   - Neon Launch plan charges $0.35/GB storage + minimal compute units.
   - **Total DB cost for 35 companies: ~$5–12/month.**

4. **Voyage AI (`voyage-4-large` + `voyage-rerank-2.5`) — ~$15–25/month (Total for 35 companies):**
   - ~15M embedding tokens/mo (`voyage-4-large` @ $0.12/1M) = $1.80
   - ~300K rerank queries/mo (`voyage-rerank-2.5` @ $0.05/1M) = $15.00
   - **Total Voyage AI cost for 35 companies: ~$16.80/month.**

5. **LLM APIs (Groq + Gemini) — ~$0–3/month:**
   - Groq Llama 3.3 70B: Free (with 5-key rotation pool).
   - Gemini 2.0 Flash: Free tier up to 15 RPM; nominal $1–3/mo overage.

### 8.2 Comprehensive Monthly Budget Summary (35 Companies Total)

| Component | Provider | Plan | Monthly Cost (USD) |
| :--- | :--- | :--- | ---: |
| **Frontend Hosting** | Vercel | Hobby (Free) | **$0.00** |
| **Backend API Server** | Render | Starter (or Free + Ping) | **$7.00** |
| **PostgreSQL + pgvector** | Neon | Launch (Pay-as-you-go) | **$8.50** |
| **Embeddings (`voyage-4-large`)** | Voyage AI | Pay-per-use | **$2.50** |
| **Reranker (`voyage-rerank-2.5`)** | Voyage AI | Pay-per-use | **$15.00** |
| **LLM Inference** | Groq + Gemini | Key Pool + Free Tiers | **$2.00** |
| **Meeting Transcription** | Fireflies.ai | Shared Business Seat | **$19.00** |
| **TOTAL ESTIMATED MONTHLY EXPENSE** | | | **~$54.00 / month** |

> **Per-Company Cost Breakdown:**  
> Total cost for 35 companies = **~$54.00/month TOTAL** (~$1.54 per company per month!).  
> Charging even a modest $99/month per company yields **$3,465/month revenue** against **$54/month infrastructure cost** (**98.4% gross margin**).

---

## 9. Infrastructure & Deployment Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                 LEAN PRODUCTION ARCHITECTURE (35 COMPANIES)         │
│                                                                     │
│  Users (35 Companies) ──▶  Vercel CDN (Free Tier - React SPA)      │
│                                  │                                  │
│                                  ▼ REST API + JWT Auth              │
│                         Render Backend ($7/mo)                      │
│                         ├─ FastAPI Server                           │
│                         ├─ Auth & Custom Visibility Filter          │
│                         └─ USIE v4 Dual Engine Router               │
│                                  │                                  │
│                 ┌────────────────┴─────────────────┐                │
│                 │                                  │                │
│          Neon PostgreSQL                     Voyage AI API          │
│          (pgvector + 35 schemas)             ├─ voyage-4-large      │
│          (~$8.50/mo)                         └─ voyage-rerank-2.5   │
│                                              (~$17.50/mo)           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 10. Security & Compliance

1. **Strict Tenant & Participant Isolation:** Every query verified against user JWT claims.
2. **Participant Access Rules:** Unauthorized queries match 0 chunks, outputting standard **"No information found."**
3. **Prompt Injection Defense:** Text-to-SQL executed strictly via parameterized `SELECT` queries validated against schema whitelists.
4. **India DPDP Act 2023 Readiness:** Consent tracking and audit log support for Indian enterprise clients.

---

## 11. Implementation Phases & Timeline

```
Phase 1: Security & Auth Hardening (Weeks 1–10)
  ├── JWT Auth Middleware & Supabase Auth integration
  ├── Audit Log table & logging on write operations
  └── Rate limiting per user/company

Phase 2: Custom Visibility & Versioning (Weeks 11–22)
  ├── Participant-Level Custom Access Rules ("Visible to All" vs "Custom")
  ├── `document_versions` schema & SHA-256 hash tracker
  ├── Self-Healing Reindexer & 30-day Legacy Version Pruner
  └── Admin Management Dashboard UI

Phase 3: Connectors & White-Label (Months 6–12)
  ├── SharePoint, Google Drive & Confluence connectors
  └── White-label custom domains & branding per tenant
```

---

## 12. Risk Register & Mitigations

| Risk | Probability | Impact | Mitigation |
| :--- | :---: | :---: | :--- |
| Unauthenticated API requests | High | Critical | **P0 Priority:** Implement JWT auth middleware. |
| Neon DB storage growth | Low | Medium | 3-stage gzip compression + 30-day legacy version chunk pruning. |
| Groq LLM rate limits | Medium | Medium | 5-key rotation pool + instant automatic Gemini 2.0 Flash fallback. |
| Participant data leakage | Low | Critical | Automated unit tests ensuring unauthorized users get 0 results ("No info found"). |

---

*Document Version 1.1 — Updated for 35-Company Scale, Custom Participant Visibility, Voyage-4-Large & Voyage-Rerank-2.5.*  
*File Location:* `c:\Users\Shobhit Shah\Desktop\RippleBot\RippleBot_PRD.md`

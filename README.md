# 🌊 RippleBot: Enterprise AI Knowledge Hub & Meeting Intelligence

RippleBot is an enterprise-grade, full-stack **RAG (Retrieval-Augmented Generation)** knowledge assistant and meeting intelligence platform. It features a **hybrid multi-tiered Excel/CSV parser**, a **dual-engine SQL + Semantic Vector Router**, streaming SSE responses, and a modern reactive UI.

---

## 🚀 Key Features

* **Zero Data-Loss Spreadsheet Ingestion (Tiers A–D):**
  * **Tier A (Row JSON):** Preserves table structure with table title context.
  * **Tier B (Markdown Windows):** Token-bounded multi-row context.
  * **Tier C (Raw Cell Archive):** Dumps 100% of non-empty cells across all sheets to ensure floating text is never lost.
  * **Tier D (Standalone Long Cells):** Automatically extracts narrative text blocks (>100 chars) as independent chunks.
* **Dual-Query Routing (SQL + Vector):**
  * **SQL Router:** Executes sub-millisecond SQLite queries (`COUNT`, `SUM`, `AVG`, `FILTER`) for exact numerical data.
  * **Vector Engine:** Semantic search via **Voyage-4-Large** embeddings and **Voyage Rerank-2.5**.
* **Real-time Voice Input (Speech-to-Text):** Integrated Web Speech API for hands-free query input.
* **Fireflies AI Meeting Integration:** Dual-tier architecture for executive meeting summaries and speaker-attributed transcript search.
* **Modern Reactive Interface:** Built with React, Vite, TanStack Router, Tailwind CSS, and Lucide icons. Supports conversation renaming, SSE streaming, document downloading, and error recovery.

---

## 🛠️ Architecture & Tech Stack

```
                     ┌────────────────────────┐
                     │   Knowledge Navigator  │
                     │  (Vite / React UI)     │
                     └───────────┬────────────┘
                                 │ HTTP / SSE
                                 ▼
                     ┌────────────────────────┐
                     │     FastAPI Engine     │
                     │    (backend/src)       │
                     └─────┬────────────┬─────┘
                           │            │
            ┌──────────────┴─┐        ┌─┴──────────────┐
            ▼                ▼        ▼                ▼
     ┌─────────────┐  ┌────────────┐ ┌──────────┐ ┌────────────┐
     │ SQLite DB   │  │ ChromaDB / │ │ Voyage   │ │ Groq / LLM │
     │ (Exact Math)│  │ pgvector   │ │ AI Rerank│ │ Inference  │
     └─────────────┘  └────────────┘ └──────────┘ └────────────┘
```

* **Frontend:** React, TypeScript, Vite, TanStack Router, Tailwind CSS, Sonner Toast.
* **Backend:** FastAPI, Python 3.10+, Uvicorn, Watchdog.
* **Database & RAG:** ChromaDB (Embedded), SQLite, Voyage AI (Voyage-4-Large, Rerank-2.5), Groq (Llama-3.3-70b), Google Gemini 2.0 Flash.

---

## ⚙️ Quick Start Guide

### 1. Prerequisites
* **Python:** 3.10 or higher
* **Node.js:** v18+ or **Bun**

### 2. Backend Setup

```bash
# Clone the repository
git clone https://github.com/shobhitrh/RippleBot.git
cd RippleBot

# Set up Python virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r backend/requirements.txt

# Create environment configuration
cp backend/.env.example backend/.env
```

Configure your `backend/.env` file with your API keys:
```env
VOYAGE_API_KEY2=your_voyage_api_key
GROQ_API_KEY=your_groq_api_key
GEMINI_API_KEY=your_gemini_api_key
```

Run the backend server:
```bash
uvicorn backend.src.main:app --reload --reload-dir backend/src --port 8000
```

### 3. Frontend Setup

```bash
# Navigate to the frontend app
cd knowledge-navigator

# Install dependencies
npm install  # or bun install

# Start the dev server
npm run dev
```

Open your browser at `http://localhost:5173`.

---

## 📋 API Endpoints Overview

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/query` | SSE Streaming endpoint for RAG chat queries. |
| `GET` | `/api/documents` | List all ingested knowledge documents & vector counts. |
| `POST` | `/api/documents/upload` | Multipart file upload & auto-indexing trigger. |
| `GET` | `/api/documents/{filename}/download` | Download original raw document file. |
| `DELETE` | `/api/documents/{filename}` | Delete document & drop embedded vector chunks. |
| `GET` | `/api/health` | Live healthcheck endpoint for DB, Vector Store & Watcher. |

---

## 📄 License

MIT License. Designed for Enterprise Knowledge Hubs.

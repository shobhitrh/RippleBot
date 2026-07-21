# Voyage AI + PostgreSQL/Pgvector RAG Integration Guide

This guide describes how to replicate, integrate, and deploy the high-performance, team-shareable RAG (Retrieval-Augmented Generation) pipeline from this codebase into another project.

---

## 📋 Architecture & Process Flow

The RAG process flow is structured as a two-stage retrieval pipeline:

```mermaid
graph TD
    A[Documents Folder] --> B[Incremental File Hash Scan]
    B -->|New/Modified| C[Semantic Document Parsers]
    C -->|Excel/PDF/Docx/MD/TXT| D[PostgreSQL Vector Database]
    E[User Query] --> F[Voyage-4-Large Embedder]
    F -->|Vector Query| G[PostgreSQL pgvector - Top 10]
    G --> H[Voyage Rerank-2.5 Re-ranker]
    H -->|Top 3 Re-ranked Chunks| I[Groq LLM Llama-3.3-70b-versatile]
    I --> J[User Response]
```

---

## 🛠️ Configuration & Models

The following Voyage models and hyperparameters are used for maximum precision:

*   **Embedding Model:** `voyage-4-large` (1536 dimensions)
*   **Re-ranking Model:** `rerank-2.5`
*   **Chunking Token Limit:** Max 800 tokens per chunk (`MAX_TOKENS = 800`)
*   **Chunk Overlap:** 120 tokens (`OVERLAP_TOKENS = 120`)
*   **First-Stage Retrieval Count:** Top 10 documents (`TOP_K = 10`)
*   **Second-Stage Re-ranked Count:** Top 3 documents (`RERANK_TOP_K = 3`)
*   **Vector Space Metric:** Cosine similarity (`vector_cosine_ops` HNSW Index)
*   **Primary Generator LLM:** `llama-3.3-70b-versatile` (via Groq)
*   **Fallback Generator LLM:** `llama-3.1-8b-instant` (via Groq)

---

## 📂 Files in this Migration Kit

You should copy the following files from this directory to your new project:

1.  [`rag_pgvector.py`](file:///rag_pgvector.py): Core RAG Engine implementation including parsers, embedding client, PostgreSQL/pgvector database integration, re-ranking logic, and answer generator.
2.  [`verify_keys.py`](file:///verify_keys.py): Utility script to test your environment variables and confirm connectivity to Voyage AI and Groq.
3.  [`eval_ragas.py`](file:///eval_ragas.py): Ragas evaluation suite for calculating faithfulness, answer relevance, context recall, and context precision.
4.  [`requirements.txt`](file:///requirements.txt): Minimal Python requirements file containing standard packages and PostgreSQL connectors (`psycopg2-binary`, `pgvector`).

---

## ✂️ Document Chunking & Parsing Strategies

The engine implements type-specific semantic parsing to ensure clean context structure for the LLM:

### 1. Markdown (`.md`)
*   **Process:** Splits files into semantic blocks using headers (`#{1,6}`), horizontal rules, bulleted/numbered lists, tables, and blank lines.
*   **Chunking:** Aggregates blocks chronologically until the accumulated tokens exceed `800`.
*   **Overlap:** Appends the last `120` tokens of the previous chunk to preserve context boundary transitions.

### 2. PDF (`.pdf`)
*   **Process:** Reads pages sequentially using `pdfplumber`.
*   **Chunking:** Keeps pages as individual chunks if they are under 800 tokens. If a page exceeds the limit, it splits the text by paragraph break boundaries (`\n\n`).
*   **Metadata:** Appends `page` and `total_pages` to metadata.

### 3. Microsoft Word (`.docx`)
*   **Process:** Extracts paragraphs and tables via `python-docx`.
*   **Tables Handling:** Table rows are parsed and formatted as structured text: `Table [Header1 | Header2]: Val1 | Val2` with column context preserved per row.
*   **Chunking:** Groups paragraphs. Tracks the current section headers (`Section X` or capitalized lines) and associates them with chunks as metadata.

### 4. Excel (`.xlsx`, `.xls`)
*   **Process:** Uses `pandas` to load all sheets.
*   **Representation:** Converts sheets into a markdown representation using `df.to_markdown()`.
*   **Chunking:** For large sheets, groups are split row-wise in blocks of 50 rows. The sheet column names are prepended to every chunk so the tabular context is never lost.

### 5. Plain Text (`.txt`)
*   **Process:** Splits content by double linebreaks (`\n\n`).
*   **Chunking:** Combines paragraphs up to 800 tokens.

---

## 🔄 Incremental Indexing Strategy

To avoid redundant API costs and slow startups, the pipeline uses MD5 hashing for change-detection:

1.  **MD5 File Hashes:** Hashes are computed for all files in the document directory.
2.  **Comparison:** The engine compares current folder hashes with hashes stored inside the PostgreSQL `documents` table.
3.  **Operations:**
    *   **New Files:** Parsed, embedded, and added to the `documents` and `chunks` tables.
    *   **Modified Files:** The previous document record is deleted from PostgreSQL by its filename, which cascades to delete all associated chunks and vector embeddings, then the new content is processed and inserted.
    *   **Deleted Files:** Corresponding records are deleted from the `documents` table (which cascade deletes the chunks).
    *   **Unchanged Files:** Completely skipped.

---

## ⚡ Integration Checklists for the Destination AI Agent

To set up this engine in your new project, perform the following steps:

### 1. Environment Configuration
Add these variables to your `.env` file:
```bash
# Voyage AI credentials (used for embedding + reranking)
VOYAGE_API_KEY2=your_voyage_api_key_here

# Groq API credentials (used for generation)
GROQ_API_KEY=your_groq_api_key_here

# PostgreSQL connection string
POSTGRES_URI=postgresql://user:password@host:port/dbname
```

### 2. Ingesting and Querying
Here is a boilerplate python snippet showing how to initialize, index, and query the system:

```python
import os
from dotenv import load_dotenv
from rag_pgvector import RAGEngine, Config

# Load keys
load_dotenv()

# Optional: Override default directories if needed
Config.DOCUMENTS_DIR = "path/to/your/documents"

# 1. Initialize RAGEngine (autodetects POSTGRES_URI or DATABASE_URL)
engine = RAGEngine()

# 2. Build or Update Index (Uses incremental MD5 checks)
print("Indexing documents...")
engine.build_index(force_rebuild=False)

# 3. Query the RAG Pipeline (Retrieve -> Voyage Rerank -> Groq LLM)
query = "What is the policy for expense reimbursements?"
result = engine.query(query, use_llm=True)

print("\n--- Answer ---")
print(result['answer'])

print("\n--- Sources ---")
for i, src in enumerate(result['sources'], 1):
    print(f"{i}. {src['metadata'].get('source_name')} (Score: {src['score']:.4f})")
```

### 3. Adapting to a Different Use Case
To customize the pipeline for a new domain:
1.  **Update the System Prompt:** Edit `_generate_answer()` in `rag_pgvector.py` to set the role, domain rules, and formatting instructions appropriate for your use case.
2.  **Adjust Chunk Size:** Modify `MAX_TOKENS` and `OVERLAP_TOKENS` inside `Config` if your new documents are more dense or structured.

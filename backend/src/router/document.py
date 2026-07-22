import os
import re
import shutil
import logging
import json
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks, Header
from backend.src import config
from backend.src.rag_engine import get_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

# Tenant header shared by all document endpoints.
CompanyId = Header(default=config.DEFAULT_COMPANY_ID, alias="X-Company-Id")


def _save_and_tag_file(file: UploadFile, metadata: dict, docs_dir: str) -> str:
    """
    Save one uploaded file into docs_dir and attach metadata (frontmatter for
    text, sidecar JSON for binary). Returns the sanitized filename. Raises on
    failure (and cleans up a partially-written file) so the caller can decide
    whether to abort (single upload) or skip-and-continue (batch upload).
    """
    safe_filename = re.sub(r'[\\/*?:"<>|]', "_", file.filename or "unnamed")
    safe_filename = re.sub(r'\s+', '_', safe_filename).strip("_") or "unnamed"
    file_path = os.path.join(docs_dir, safe_filename)

    # Save the raw bytes.
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        logger.info(f"Saved uploaded file to {file_path}")
    except Exception as e:
        logger.error(f"Failed to save uploaded file '{safe_filename}': {e}")
        raise

    # Attach metadata.
    _, ext = os.path.splitext(safe_filename)
    is_text = ext.lower() in [".md", ".txt"]
    try:
        if is_text:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            # Strip any existing frontmatter to avoid doubling it.
            content_clean = content
            match = re.match(r'^---\s*\n.*?\n---\s*\n', content, re.DOTALL)
            if match:
                content_clean = content[match.end():]
            frontmatter = f"""---
department: {metadata['department']}
uploaded_by: {metadata['uploaded_by']}
category: {metadata['category']}
uploaded_at: {metadata['uploaded_at']}
---

"""
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(frontmatter + content_clean)
            logger.info(f"Prepended frontmatter metadata to text document: {file_path}")
        else:
            sidecar_path = file_path + ".metadata.json"
            with open(sidecar_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
            logger.info(f"Created sidecar metadata JSON: {sidecar_path}")
    except Exception as e:
        logger.error(f"Failed to attach metadata to '{safe_filename}': {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        raise

    return safe_filename


def _trigger_index(company_id: str, label: str = ""):
    """Kick off an incremental index build for a tenant. Idempotent and locked
    inside the engine, so it's safe to call once after a batch of saves."""
    try:
        engine = get_engine(company_id, required=False)
        if engine is None:
            logger.warning("Skipping index build — vector store is unavailable.")
            return
        logger.info(f"Triggering index build (tenant={company_id}){' for ' + label if label else ''}")
        engine.build_index(force_rebuild=False)
    except Exception as e:
        logger.error(f"Index build trigger failed: {e}")


@router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    department: Optional[str] = Form(None),
    uploaded_by: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    company_id: str = CompanyId,
):
    """
    Accept a single multipart/form-data upload, save it to the company's
    knowledge_base subfolder, attach metadata, and trigger indexing.
    """
    company_id = config.normalize_company_id(company_id)
    docs_dir = config.company_documents_dir(company_id)
    metadata = {
        "department": department or "General",
        "uploaded_by": uploaded_by or "System",
        "category": category or "Document",
        "uploaded_at": datetime.utcnow().isoformat(),
    }
    try:
        safe_filename = _save_and_tag_file(file, metadata, docs_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save file: {str(e)}")

    background_tasks.add_task(_trigger_index, company_id, safe_filename)

    return {
        "status": "success",
        "message": f"Document '{safe_filename}' uploaded successfully. Indexing started.",
        "filename": safe_filename,
        "metadata": metadata,
    }


@router.post("/upload-batch")
async def upload_documents(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    department: Optional[str] = Form(None),
    uploaded_by: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    company_id: str = CompanyId,
):
    """
    Ingest MANY files in one request. Each file is saved independently — a single
    bad file is skipped and reported rather than failing the whole batch — and the
    index is rebuilt exactly once after all saves (not once per file).
    """
    company_id = config.normalize_company_id(company_id)
    docs_dir = config.company_documents_dir(company_id)
    metadata = {
        "department": department or "General",
        "uploaded_by": uploaded_by or "System",
        "category": category or "Document",
        "uploaded_at": datetime.utcnow().isoformat(),
    }

    saved: List[str] = []
    failed: List[dict] = []
    for f in files:
        try:
            saved.append(_save_and_tag_file(f, dict(metadata), docs_dir))
        except Exception as e:
            failed.append({"filename": f.filename, "error": str(e)})

    # One index pass for the whole batch (only if anything landed).
    if saved:
        background_tasks.add_task(_trigger_index, company_id, f"{len(saved)} file(s)")

    return {
        "status": "success" if not failed else ("partial" if saved else "error"),
        "message": (
            f"{len(saved)} file(s) uploaded successfully. Indexing started."
            + (f" {len(failed)} failed." if failed else "")
        ),
        "saved": saved,
        "failed": failed,
        "metadata": metadata,
    }

@router.post("/import-fireflies")
async def import_fireflies_meeting(
    background_tasks: BackgroundTasks,
    meeting_id: str = Form(...),
    company_id: str = CompanyId,
):
    """
    Import a past Fireflies meeting by transcript ID into the selected company.
    Internal app action (auth = X-Company-Id header, CORS-locked) — reuses the same
    fetch/summary/index pipeline as the webhook, pinned to this tenant.
    """
    from backend.src.router.webhook import process_meeting
    cid = config.normalize_company_id(company_id)
    mid = (meeting_id or "").strip()
    if not mid:
        raise HTTPException(status_code=400, detail="meeting_id is required")
    background_tasks.add_task(process_meeting, mid, cid)
    return {"status": "processing", "company_id": cid, "meeting_id": mid}

@router.get("")
async def list_documents(company_id: str = CompanyId):
    """
    List this company's files with their index status and vector counts.
    """
    company_id = config.normalize_company_id(company_id)
    docs_dir = config.company_documents_dir(company_id)
    if not os.path.exists(docs_dir):
        return []

    # Get all files in directory, ignoring sidecar metadata and hidden files
    all_files = []
    for f in os.listdir(docs_dir):
        full_path = os.path.join(docs_dir, f)
        if os.path.isfile(full_path) and not f.startswith(".") and not f.startswith("~$") and not f.endswith(".metadata.json"):
            all_files.append(f)

    # Fetch index status from the vector store (backend-agnostic). If the store
    # is offline we simply show the physical files with a "pending" status.
    db_docs = {}
    engine = get_engine(company_id, required=False)
    if engine is not None:
        try:
            db_docs = engine.list_indexed_documents()
        except Exception as e:
            logger.error(f"Could not load indexed-document metadata: {e}")

    result = []
    for filename in all_files:
        full_path = os.path.join(docs_dir, filename)
        stat = os.stat(full_path)
        
        # Merge physical files with database record if it exists
        doc_info = {
            "filename": filename,
            "path": f"./backend/knowledge_base/{filename}",
            "size": stat.st_size,
            "modified": datetime.utcfromtimestamp(stat.st_mtime).isoformat(),
            "department": "General",
            "uploaded_by": "System",
            "category": "Document",
            "index_status": "pending",
            "error_message": None,
            "vector_count": 0
        }
        
        if filename in db_docs:
            doc_info.update(db_docs[filename])
            
        result.append(doc_info)
        
    return result

@router.delete("/{filename}")
async def delete_document(filename: str, company_id: str = CompanyId):
    """
    Remove this company's physical file + sidecar and drop its vectors/records.
    """
    company_id = config.normalize_company_id(company_id)
    docs_dir = config.company_documents_dir(company_id)
    # Sanitize filename to prevent directory traversal
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(docs_dir, safe_filename)
    sidecar_path = file_path + ".metadata.json"
    
    file_exists = os.path.exists(file_path)
    
    # Try deleting physical files
    deleted_physically = False
    try:
        if file_exists:
            os.remove(file_path)
            deleted_physically = True
            logger.info(f"Deleted physical document file: {file_path}")
        if os.path.exists(sidecar_path):
            os.remove(sidecar_path)
            logger.info(f"Deleted sidecar metadata file: {sidecar_path}")
    except Exception as e:
        logger.error(f"Error removing physical files for {safe_filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not delete physical files: {str(e)}")

    # Delete vectors/records from the store (best-effort; a down store shouldn't
    # block removing the physical file).
    engine = get_engine(company_id, required=False)
    if engine is not None:
        try:
            removed = engine.delete_document(safe_filename)
            logger.info(f"Deleted {removed} indexed chunk(s) for: {safe_filename}")
        except Exception as e:
            logger.error(f"Error deleting index records for {safe_filename}: {e}")

    if not file_exists and not deleted_physically:
        # File wasn't in directory, but we cleaned up DB just in case
        return {"status": "success", "message": f"Cleaned up database records for '{safe_filename}'."}
        
    return {"status": "success", "message": f"Document '{safe_filename}' and all its embeddings deleted successfully."}

@router.post("/{filename}/assign")
async def assign_document(filename: str, target_company_id: str = Form(...), company_id: str = CompanyId):
    """
    Move a document (e.g. an 'unassigned' Fireflies meeting) from the source tenant
    to the target company: relocate the file + sidecar, drop it from the source
    index, and re-index it under the target. Fixes/rescues mis-routed meetings.
    """
    src = config.normalize_company_id(company_id)
    dst = config.normalize_company_id(target_company_id)
    if src == dst:
        return {"status": "noop", "message": "Source and target company are the same."}

    safe = os.path.basename(filename)
    src_dir = config.company_documents_dir(src)
    dst_dir = config.company_documents_dir(dst)
    src_file = os.path.join(src_dir, safe)
    if not os.path.exists(src_file):
        raise HTTPException(status_code=404, detail="File not found in source company")

    # Move file + sidecar to the target tenant's folder.
    try:
        shutil.move(src_file, os.path.join(dst_dir, safe))
        sidecar = src_file + ".metadata.json"
        if os.path.exists(sidecar):
            shutil.move(sidecar, os.path.join(dst_dir, safe + ".metadata.json"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not move file: {e}")

    # Drop from source index, then index under the target.
    src_engine = get_engine(src, required=False)
    if src_engine is not None:
        try:
            src_engine.delete_document(safe)
        except Exception as e:
            logger.error(f"assign: failed to de-index from source '{src}': {e}")

    def reindex_target():
        try:
            eng = get_engine(dst, required=False)
            if eng is not None:
                eng.build_index(force_rebuild=False)
        except Exception:
            logger.error(f"assign: reindex failed for target '{dst}'", exc_info=True)
    reindex_target()

    return {"status": "success", "message": f"Moved '{safe}' from {src} to {dst}", "company_id": dst}


@router.get("/{filename}/preview")
async def get_document_preview(filename: str, company_id: str = CompanyId):
    """Read the first few kilobytes of the file and return it as preview text."""
    company_id = config.normalize_company_id(company_id)
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(config.company_documents_dir(company_id), safe_filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    _, ext = os.path.splitext(safe_filename)
    is_text = ext.lower() in [".md", ".txt"]
    
    ext_l = ext.lower()
    try:
        if is_text:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(5000)  # Read up to 5k chars
            return {"preview": content or "(empty file)"}

        elif ext_l == ".pdf":
            import pdfplumber
            parts = []
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages[:5], 1):
                    text = page.extract_text() or ""
                    if text.strip():
                        parts.append(f"## Page {i}\n{text}")
                    if sum(len(p) for p in parts) > 5000:
                        break
            preview = "\n\n".join(parts)[:5000]
            return {"preview": preview or "PDF has no extractable text (it may be scanned/image-only)."}

        elif ext_l == ".docx":
            import docx
            with open(file_path, "rb") as f:
                doc = docx.Document(f)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()][:40]
            preview = "\n\n".join(paragraphs)[:5000]
            return {"preview": preview or "(document has no readable paragraphs)"}

        elif ext_l in (".xlsx", ".xls"):
            from backend.src.excel_parser import process_excel_file
            try:
                chunks, sqlite_tables = process_excel_file(file_path)
                parts = []
                for table_name, df in sqlite_tables[:20]:
                    name_parts = table_name.split("_")
                    sheet_lbl = name_parts[-2] if len(name_parts) >= 3 else "Sheet"
                    tbl_lbl = name_parts[-1] if len(name_parts) >= 3 else "Table"
                    
                    df_preview = df.iloc[:500].fillna("")
                    table_md = df_preview.to_markdown(index=False)
                    parts.append(f"## Sheet: {sheet_lbl} ({tbl_lbl})\n{table_md}")
                    if len(df) > 500:
                        parts.append(f"\n*Note: Table truncated. Showing first 500 rows. Download the original file to view the remaining {len(df) - 500} rows.*")
                preview = "\n\n".join(parts)
            except Exception as ex:
                logger.error(f"Failed parsing excel for preview: {ex}")
                preview = f"Error loading preview: {str(ex)}"
            return {"preview": preview or "(spreadsheet is empty)"}

        elif ext_l in (".csv", ".tsv"):
            from backend.src.excel_parser import process_csv_file
            try:
                chunks, sqlite_tables = process_csv_file(file_path)
                parts = []
                for table_name, df in sqlite_tables[:1]:
                    df_preview = df.iloc[:100].fillna("")
                    table_md = df_preview.to_markdown(index=False)
                    parts.append(table_md)
                    if len(df) > 100:
                        parts.append(f"\n*Note: Table truncated. Showing first 100 rows. Download the original file to view the remaining {len(df) - 100} rows.*")
                preview = "\n\n".join(parts)
            except Exception as ex:
                logger.error(f"Failed parsing csv for preview: {ex}")
                preview = f"Error loading preview: {str(ex)}"
            return {"preview": preview or "(csv is empty)"}

        elif ext_l in (".json", ".yaml", ".yml", ".log", ".xml", ".html"):
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                preview = f.read(5000) or "(empty file)"
            return {"preview": preview}

        else:
            # Last-resort: attempt a UTF-8 text read; if it decodes cleanly, show it.
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    sample = f.read(5000)
                if sample.strip():
                    return {"preview": sample}
            except Exception:
                pass
            size_kb = max(1, round(os.path.getsize(file_path) / 1024))
            return {"preview": f"No text preview available for {ext or 'this'} files ({size_kb} KB). "
                               f"The file is indexed and searchable, but its binary contents can't be shown here."}
    except Exception as e:
        logger.error(f"Error generating preview for {safe_filename}: {e}")
        return {"preview": f"Error loading preview: {str(e)}"}

@router.get("/{filename}/download")
async def download_document(filename: str, company_id: str = CompanyId):
    """Download the raw file from the company's knowledge_base."""
    from fastapi.responses import FileResponse
    company_id = config.normalize_company_id(company_id)
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(config.company_documents_dir(company_id), safe_filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    return FileResponse(
        path=file_path,
        filename=safe_filename,
        media_type="application/octet-stream"
    )

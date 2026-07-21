import os
import re
import shutil
import logging
import json
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from backend.src import config
from backend.src.rag_engine import get_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

@router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    department: Optional[str] = Form(None),
    uploaded_by: Optional[str] = Form(None),
    category: Optional[str] = Form(None)
):
    """
    Accept multipart/form-data upload, save the file to knowledge_base,
    and attach metadata via frontmatter (text) or sidecar JSON (binary).
    """
    filename = file.filename
    # Sanitize filename
    safe_filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
    safe_filename = re.sub(r'\s+', '_', safe_filename).strip("_")
    
    file_path = os.path.join(config.DOCUMENTS_DIR, safe_filename)
    
    # Save the file
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        logger.info(f"Saved uploaded file to {file_path}")
    except Exception as e:
        logger.error(f"Failed to save uploaded file: {e}")
        raise HTTPException(status_code=500, detail=f"Could not save file: {str(e)}")

    # Construct metadata
    metadata = {
        "department": department or "General",
        "uploaded_by": uploaded_by or "System",
        "category": category or "Document",
        "uploaded_at": datetime.utcnow().isoformat()
    }

    _, ext = os.path.splitext(safe_filename)
    is_text = ext.lower() in [".md", ".txt"]

    try:
        if is_text:
            # For text files, read content and prepend frontmatter
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # If it already has frontmatter, we strip it first to avoid double frontmatter
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
            # For binary files, save a sidecar metadata JSON file
            sidecar_path = file_path + ".metadata.json"
            with open(sidecar_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
            logger.info(f"Created sidecar metadata JSON: {sidecar_path}")
            
    except Exception as e:
        logger.error(f"Failed to append metadata to file: {e}")
        # Clean up file on failure
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"Failed to add metadata: {str(e)}")

    # Watcher will pick this up automatically, but we can also manually kick off a build task
    # to guarantee fast feedback loop
    def run_indexer():
        try:
            engine = get_engine(required=False)
            if engine is None:
                logger.warning("Skipping index build — vector store is unavailable.")
                return
            logger.info(f"Triggering direct index from upload for: {safe_filename}")
            engine.build_index(force_rebuild=False)
        except Exception as e:
            logger.error(f"Manual index build trigger failed: {e}")
            
    background_tasks.add_task(run_indexer)

    return {
        "status": "success",
        "message": f"Document '{safe_filename}' uploaded successfully. Indexing started.",
        "filename": safe_filename,
        "metadata": metadata
    }

@router.get("")
async def list_documents():
    """
    List all files present in ./backend/knowledge_base/
    with their index status and vector counts from the database.
    """
    if not os.path.exists(config.DOCUMENTS_DIR):
        return []
        
    # Get all files in directory, ignoring sidecar metadata and hidden files
    all_files = []
    for f in os.listdir(config.DOCUMENTS_DIR):
        full_path = os.path.join(config.DOCUMENTS_DIR, f)
        if os.path.isfile(full_path) and not f.startswith(".") and not f.startswith("~$") and not f.endswith(".metadata.json"):
            all_files.append(f)
            
    # Fetch index status from the vector store (backend-agnostic). If the store
    # is offline we simply show the physical files with a "pending" status.
    db_docs = {}
    engine = get_engine(required=False)
    if engine is not None:
        try:
            db_docs = engine.list_indexed_documents()
        except Exception as e:
            logger.error(f"Could not load indexed-document metadata: {e}")

    result = []
    for filename in all_files:
        full_path = os.path.join(config.DOCUMENTS_DIR, filename)
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
async def delete_document(filename: str):
    """
    Remove physical file and metadata sidecars,
    and drop document records and vectors from PostgreSQL.
    """
    # Sanitize filename to prevent directory traversal
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(config.DOCUMENTS_DIR, safe_filename)
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
    engine = get_engine(required=False)
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

@router.get("/{filename}/preview")
async def get_document_preview(filename: str):
    """Read the first few kilobytes of the file and return it as preview text."""
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(config.DOCUMENTS_DIR, safe_filename)
    
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
async def download_document(filename: str):
    """Download the raw file from knowledge_base."""
    from fastapi.responses import FileResponse
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(config.DOCUMENTS_DIR, safe_filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    return FileResponse(
        path=file_path,
        filename=safe_filename,
        media_type="application/octet-stream"
    )

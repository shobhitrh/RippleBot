import os
import time
import threading
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from backend.src import config
from backend.src.rag_engine import get_engine

logger = logging.getLogger(__name__)

class DocumentHandler(FileSystemEventHandler):
    def __init__(self, debounce_seconds: float = 2.0):
        self.debounce_seconds = debounce_seconds
        self.timer = None
        self.lock = threading.Lock()
        self._pending_companies = set()  # tenants with changes awaiting index

    def _company_from_path(self, path: str) -> str:
        """Infer the tenant from the path: <knowledge_base>/<company_id>/<file>."""
        try:
            rel = os.path.relpath(path, config.DOCUMENTS_DIR)
            first = rel.replace("\\", "/").split("/")[0]
            if first and first not in (".", "..", "db"):
                return config.normalize_company_id(first)
        except Exception:
            pass
        return config.DEFAULT_COMPANY_ID

    def trigger_indexing(self, company_id: str):
        with self.lock:
            self._pending_companies.add(company_id)
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(self.debounce_seconds, self._run_indexing)
            self.timer.start()

    def _run_indexing(self):
        with self.lock:
            companies = list(self._pending_companies)
            self._pending_companies.clear()
        logger.info(f"Watcher: quiet period reached. Indexing tenants: {companies}")
        for cid in companies:
            try:
                engine = get_engine(cid, required=False)
                if engine is None:
                    logger.warning(f"Watcher: vector store unavailable — skipping tenant '{cid}'.")
                    continue
                if engine.build_index(force_rebuild=False):
                    logger.info(f"Watcher: Indexing complete for tenant '{cid}'.")
            except Exception as e:
                logger.error(f"Watcher: Error building index for tenant '{cid}': {e}")

    def on_any_event(self, event):
        if event.is_directory:
            return

        path = event.src_path
        filename = os.path.basename(path)

        # Ignore hidden, temporary and metadata files
        if filename.startswith(".") or filename.startswith("~$") or filename.endswith(".metadata.json"):
            return

        supported_extensions = {'.xlsx', '.xls', '.pdf', '.md', '.txt', '.docx', '.csv', '.tsv'}
        _, ext = os.path.splitext(filename)

        if ext.lower() in supported_extensions:
            company_id = self._company_from_path(path)
            logger.info(f"Watcher: Detected '{event.event_type}' on {path} (tenant={company_id})")
            self.trigger_indexing(company_id)


_observer = None

def start_watcher():
    global _observer
    if _observer:
        logger.warning("Watcher is already running.")
        return
        
    logger.info(f"Watcher: Starting observer on directory: {config.DOCUMENTS_DIR}")
    event_handler = DocumentHandler()
    _observer = Observer()
    _observer.schedule(event_handler, config.DOCUMENTS_DIR, recursive=True)
    _observer.start()

def stop_watcher():
    global _observer
    if _observer:
        logger.info("Watcher: Stopping observer...")
        _observer.stop()
        _observer.join()
        _observer = None
        logger.info("Watcher: Stopped.")

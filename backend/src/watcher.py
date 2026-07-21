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
    def trigger_indexing(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(self.debounce_seconds, self._run_indexing)
            self.timer.start()
            
    def _run_indexing(self):
        logger.info("Watcher: Change quiet period reached. Starting incremental index...")
        try:
            engine = get_engine(required=False)
            if engine is None:
                logger.warning("Watcher: vector store unavailable — skipping index build.")
                return
            success = engine.build_index(force_rebuild=False)
            if success:
                logger.info("Watcher: Indexing complete.")
            else:
                logger.warning("Watcher: Indexing finished with warnings/no operations.")
        except Exception as e:
            logger.error(f"Watcher: Error building index: {e}")

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
            logger.info(f"Watcher: Detected event '{event.event_type}' on file: {path}")
            self.trigger_indexing()


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

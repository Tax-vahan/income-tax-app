import json
import os
import threading
from datetime import datetime
from typing import Optional, Dict, List
from conso.utils.logger import get_logger

logger = get_logger(__name__)

class ConsoService:
    def __init__(self, conso_file: str, jobs_lock: threading.Lock, data_dir: str, downloads_dir: str):
        self.conso_file = conso_file
        self.jobs_lock = jobs_lock
        self.data_dir = data_dir
        self.downloads_dir = downloads_dir
        self.requests: Dict = {}
        self._save_pending = False
        self._save_debounce_lock = threading.Lock()
        self._load_requests()

    def _load_requests(self):
        """Load CONSO requests from JSON file"""
        if os.path.exists(self.conso_file):
            try:
                with open(self.conso_file, 'r') as f:
                    self.requests = json.load(f)
                logger.info(f"Loaded {len(self.requests)} CONSO requests")
            except Exception as e:
                logger.error(f"Error loading CONSO requests: {e}")
                self.requests = {}
        else:
            self.requests = {}

    def _save_requests(self):
        """Save CONSO requests to JSON file (called outside jobs_lock)"""
        with self.jobs_lock:
            snapshot = dict(self.requests)
        try:
            with open(self.conso_file, 'w') as f:
                json.dump(snapshot, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving CONSO requests: {e}")

    def _save_requests_debounced(self):
        """Coalesce rapid writes into one flush ~2s later instead of blocking every caller"""
        with self._save_debounce_lock:
            if self._save_pending:
                return
            self._save_pending = True

        def _do_flush():
            import time
            time.sleep(2.0)
            self._save_pending = False
            self._save_requests()

        threading.Thread(target=_do_flush, daemon=True).start()

    async def get_quarters(self, financial_year: str) -> list:
        """Get available quarters"""
        return [{"code": "3", "description": "Q1", "displayName": "Q1"}]

    async def initiate_download(self, tan: str, financial_year: str, quarter: str, form_type: str) -> dict:
        """Initiate CONSO file download"""
        import uuid
        request_id = f"CONSO_{tan}_{financial_year}_{quarter}_{uuid.uuid4().hex[:8]}"

        logger.info(f"Initiating CONSO download: {request_id}")

        request_data = {
            "request_id": request_id,
            "tan": tan,
            "financial_year": financial_year,
            "quarter": quarter,
            "form_type": form_type,
            "status": "INITIATED",
            "initiated_date": datetime.utcnow().isoformat(),
            "completed_date": None,
            "file_path": None,
            "created_at": datetime.utcnow().isoformat(),
        }

        with self.jobs_lock:
            self.requests[request_id] = request_data
        self._save_requests_debounced()

        return {"request_id": request_id, "status": "INITIATED", "message": "CONSO download initiated"}

    async def get_status(self, request_id: str) -> dict:
        """Check status of CONSO download"""
        with self.jobs_lock:
            req = self.requests.get(request_id)

        if req is None:
            return {
                "request_id": request_id,
                "status": "NOT_FOUND",
                "is_ready": False,
                "message": "Request not found",
                "progress": 0,
            }

        return {
            "request_id": request_id,
            "status": req.get("status", "UNKNOWN"),
            "is_ready": req.get("status") == "COMPLETED",
            "message": req.get("status", ""),
            "progress": 100 if req.get("status") == "COMPLETED" else 50,
        }

    async def list_requests(self, tan: str, page: int = 0, page_size: int = 10) -> dict:
        """List CONSO requests for TAN"""
        with self.jobs_lock:
            requests = [r for r in self.requests.values() if r.get("tan") == tan]
        total = len(requests)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        start = page * page_size
        end = start + page_size

        return {
            "requests": requests[start:end],
            "current_page": page,
            "total_pages": total_pages,
            "total_elements": total,
            "page_size": page_size,
            "has_next": page < total_pages - 1 if total_pages > 0 else False,
            "has_previous": page > 0,
            "message": "CONSO requests retrieved",
        }

class ConsoServiceFactory:
    _instance: Optional['ConsoService'] = None

    @classmethod
    def set_instance(cls, instance: 'ConsoService'):
        cls._instance = instance
        logger.info("ConsoService instance set")

    @classmethod
    def get_instance(cls) -> 'ConsoService':
        if cls._instance is None:
            raise RuntimeError("ConsoService not initialized")
        return cls._instance

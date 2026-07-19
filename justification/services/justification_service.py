import json
import os
import threading
from datetime import datetime
from typing import Optional, Dict
from justification.utils.logger import get_logger

logger = get_logger(__name__)

class JustificationService:
    def __init__(self, justification_file: str, jobs_lock: threading.Lock, data_dir: str, downloads_dir: str):
        self.justification_file = justification_file
        self.jobs_lock = jobs_lock
        self.data_dir = data_dir
        self.downloads_dir = downloads_dir
        self.requests: Dict = {}
        self._save_pending = False
        self._save_debounce_lock = threading.Lock()
        self._load_requests()

    def _load_requests(self):
        """Load justification requests from JSON file"""
        if os.path.exists(self.justification_file):
            try:
                with open(self.justification_file, 'r') as f:
                    self.requests = json.load(f)
                logger.info(f"Loaded {len(self.requests)} justification requests")
            except Exception as e:
                logger.error(f"Error loading justification requests: {e}")
                self.requests = {}
        else:
            self.requests = {}

    def _save_requests(self):
        """Save justification requests to JSON file (called outside jobs_lock)"""
        with self.jobs_lock:
            snapshot = dict(self.requests)
        try:
            with open(self.justification_file, 'w') as f:
                json.dump(snapshot, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving justification requests: {e}")

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

    async def get_financial_years(self) -> list:
        """Get available financial years"""
        return [{"code": "2026", "description": "2026-27", "yearValue": 2026}]

    async def get_form_types(self, tan: str, financial_year: str) -> list:
        """Get available form types for TAN"""
        # Returns empty if no data (matches TRACES 404 behavior)
        return []

    async def initiate_report(self, tan: str, financial_year: str, form_type: str) -> dict:
        """Initiate justification report download"""
        import uuid
        request_id = f"JUST_{tan}_{financial_year}_{form_type}_{uuid.uuid4().hex[:8]}"

        logger.info(f"Initiating justification report: {request_id}")

        request_data = {
            "request_id": request_id,
            "tan": tan,
            "financial_year": financial_year,
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

        return {"request_id": request_id, "status": "INITIATED", "message": "Justification report initiated"}

    async def get_status(self, request_id: str) -> dict:
        """Check status of justification report"""
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

    async def list_reports(self, tan: str, page: int = 0, page_size: int = 10) -> dict:
        """List justification reports for TAN"""
        with self.jobs_lock:
            completed = [r for r in self.requests.values() if r.get("tan") == tan and r.get("status") == "COMPLETED"]
        total = len(completed)
        start = page * page_size
        end = start + page_size
        return {"reports": completed[start:end], "total": total, "page": page, "message": "Reports retrieved"}

class JustificationServiceFactory:
    _instance: Optional['JustificationService'] = None

    @classmethod
    def set_instance(cls, instance: 'JustificationService'):
        cls._instance = instance
        logger.info("JustificationService instance set")

    @classmethod
    def get_instance(cls) -> 'JustificationService':
        if cls._instance is None:
            raise RuntimeError("JustificationService not initialized")
        return cls._instance

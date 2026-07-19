import json
import os
import threading
from datetime import datetime
from typing import Optional, Dict
from tdstcs.utils.logger import get_logger

logger = get_logger(__name__)


class TDSTCSService:
    def __init__(self, tdstcs_file: str, jobs_lock: threading.Lock, data_dir: str, downloads_dir: str):
        self.tdstcs_file = tdstcs_file
        self.jobs_lock = jobs_lock
        self.data_dir = data_dir
        self.downloads_dir = downloads_dir
        self.requests: Dict = {}
        self._save_pending = False
        self._save_debounce_lock = threading.Lock()
        self._load_requests()

    def _load_requests(self):
        """Load TDS/TCS requests from JSON file"""
        if os.path.exists(self.tdstcs_file):
            try:
                with open(self.tdstcs_file, 'r') as f:
                    self.requests = json.load(f)
                logger.info(f"Loaded {len(self.requests)} TDS/TCS requests from {self.tdstcs_file}")
            except Exception as e:
                logger.error(f"Error loading TDS/TCS requests: {e}")
                self.requests = {}
        else:
            self.requests = {}
            logger.info("No existing TDS/TCS requests file, starting fresh")

    def _save_requests(self):
        """Save TDS/TCS requests to JSON file (thread-safe, called outside jobs_lock)"""
        with self.jobs_lock:
            snapshot = dict(self.requests)
        try:
            with open(self.tdstcs_file, 'w') as f:
                json.dump(snapshot, f, indent=2)
            logger.debug(f"Saved {len(snapshot)} TDS/TCS requests")
        except Exception as e:
            logger.error(f"Error saving TDS/TCS requests: {e}")

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

    def _patch_request(self, request_id: str, updates: dict):
        """Update request with thread safety"""
        with self.jobs_lock:
            if request_id in self.requests:
                self.requests[request_id].update(updates)
                self.requests[request_id]['updated_at'] = datetime.utcnow().isoformat()
        self._save_requests_debounced()

    async def get_form_types(self) -> list:
        """Get available form types from TRACES API"""
        # In production, this would call TRACES API
        # For now, return mock data (131, 133 are the valid ones per error message)
        return [
            {"code": "131", "description": "Form No. 131"},
            {"code": "133", "description": "Form No. 133"},
        ]

    async def get_quarters(self, financial_year: str) -> list:
        """Get available quarters for a financial year"""
        # In production, this would call TRACES API
        return [
            {"code": "3", "description": "Q1", "displayName": "Q1"},
            {"code": "4", "description": "Q2", "displayName": "Q2"},
            {"code": "1", "description": "Q3", "displayName": "Q3"},
            {"code": "2", "description": "Q4", "displayName": "Q4"},
        ]

    async def initiate_certificate_download(
        self, tan: str, financial_year: str, quarter: str, form_type: str
    ) -> dict:
        """Initiate a TDS/TCS certificate download request"""
        import uuid
        import time

        request_id = f"TDSTCS_{tan}_{financial_year.replace('-', '_')}_{quarter}_{uuid.uuid4().hex[:8]}"

        logger.info(
            f"Initiating TDS/TCS download: {request_id} for {tan} ({form_type}, {financial_year}, {quarter})"
        )

        request_data = {
            "request_id": request_id,
            "tan": tan,
            "financial_year": financial_year,
            "quarter": quarter,
            "form_type": form_type,
            "status": "INITIATED",
            "processing_status": "Queued for download",
            "initiated_date": datetime.utcnow().isoformat(),
            "completed_date": None,
            "file_path": None,
            "created_at": datetime.utcnow().isoformat(),
        }

        with self.jobs_lock:
            self.requests[request_id] = request_data
        self._save_requests_debounced()

        return {
            "request_id": request_id,
            "status": "INITIATED",
            "message": "Certificate download initiated",
            "transaction_id": None,
        }

    async def get_status(self, request_id: str) -> dict:
        """Check status of a certificate download request"""
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
            "message": req.get("processing_status", ""),
            "progress": 100 if req.get("status") == "COMPLETED" else 50,
            "file_path": req.get("file_path"),
        }

    async def list_completed_certificates(
        self, tan: str, page: int = 0, page_size: int = 10
    ) -> dict:
        """List completed certificate downloads for a TAN"""
        with self.jobs_lock:
            completed = [
                req
                for req in self.requests.values()
                if req.get("tan") == tan and req.get("status") == "COMPLETED"
            ]

        total = len(completed)
        start = page * page_size
        end = start + page_size

        return {
            "certificates": completed[start:end],
            "total": total,
            "page": page,
            "message": "Completed certificates retrieved",
        }


class TDSTCSServiceFactory:
    """Singleton factory for TDSTCSService"""

    _instance: Optional[TDSTCSService] = None

    @classmethod
    def set_instance(cls, instance: TDSTCSService):
        cls._instance = instance
        logger.info("TDSTCSService instance set")

    @classmethod
    def get_instance(cls) -> TDSTCSService:
        if cls._instance is None:
            raise RuntimeError("TDSTCSService not initialized. Call set_instance() first.")
        return cls._instance

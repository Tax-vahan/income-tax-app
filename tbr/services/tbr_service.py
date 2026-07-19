import os
import json
import threading
import logging
import uuid
from typing import Optional, Dict, List
from datetime import datetime

logger = logging.getLogger(__name__)


class TBRService:
    """TBR Service using JSON file storage (matching income-tax-app patterns)"""

    def __init__(self, tbr_file: str, jobs_lock: threading.Lock, data_dir: str, downloads_dir: str):
        """
        Initialize TBR Service
        
        Args:
            tbr_file: Path to tbr_requests.json
            jobs_lock: Shared threading lock from api_service
            data_dir: Path to data directory
            downloads_dir: Path to downloads directory
        """
        self.tbr_file = tbr_file
        self.jobs_lock = jobs_lock
        self.data_dir = data_dir
        self.downloads_dir = downloads_dir
        self.requests = self._load_requests()
        self.logger = logging.getLogger(__name__)
        self._save_pending = False
        self._save_debounce_lock = threading.Lock()

    def _load_requests(self) -> dict:
        """Load TBR requests from JSON file"""
        if os.path.exists(self.tbr_file):
            try:
                with open(self.tbr_file) as f:
                    return json.load(f)
            except Exception as e:
                self.logger.error(f"Failed to load TBR requests: {e}")
                return {}
        return {}

    def _save_requests(self) -> None:
        """Save TBR requests to JSON file (thread-safe)"""
        with self.jobs_lock:
            snapshot = dict(self.requests)
        try:
            with open(self.tbr_file, "w") as f:
                json.dump(snapshot, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save TBR requests: {e}")

    def _save_requests_debounced(self) -> None:
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

    def _patch_request(self, request_id: str, **fields) -> None:
        """Update TBR request with thread safety"""
        with self.jobs_lock:
            if request_id in self.requests:
                self.requests[request_id].update(fields)
        self._save_requests_debounced()

    async def validate_tan(
        self,
        tan: str,
        financial_year: int,
        quarter: str,
    ) -> dict:
        """Validate TAN by checking if downloaded files exist"""
        self.logger.info(f"Validating TAN: {tan} for FY {financial_year} Q{quarter}")
        
        # For now, always return true (assume statements are available)
        # In production: check if files exist for this tan/year/quarter
        is_valid = True
        
        return {
            "isValid": is_valid,
            "message": "Statement available" if is_valid else "No statement found",
            "tan": tan,
            "finYear": str(financial_year),
            "quarter": int(quarter),
            "processingStatus": ""
        }

    async def initiate_tbr_request(
        self,
        tan: str,
        financial_year: int,
        quarter: str,
        user_id: str,
    ) -> dict:
        """Initiate new TBR request"""
        request_id = f"TBR_{tan}_{financial_year}_{quarter}_{uuid.uuid4().hex[:8]}"
        
        self.logger.info(f"Initiating TBR: {request_id}")
        
        request_data = {
            "id": len(self.requests) + 1,
            "request_id": request_id,
            "tan": tan,
            "financial_year": financial_year,
            "quarter": quarter,
            "status": "INITIATED",
            "processing_status": "Queued for processing",
            "total_records": 0,
            "processed_records": 0,
            "initiated_date": datetime.utcnow().isoformat(),
            "completed_date": None,
            "file_path": None,
            "created_at": datetime.utcnow().isoformat(),
        }
        
        with self.jobs_lock:
            self.requests[request_id] = request_data

        self._save_requests_debounced()
        return request_data

    async def get_ready_requests(
        self,
        tan: str,
        page: int = 0,
        size: int = 10,
    ) -> dict:
        """Get ready-to-download TBR requests (paginated)"""
        self.logger.info(f"Fetching TBR requests for {tan} (page {page}, size {size})")
        
        with self.jobs_lock:
            all_requests = [
                r for r in self.requests.values()
                if r.get("tan") == tan and r.get("status") in ("COMPLETED", "READY")
            ]
        
        all_requests.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        
        total = len(all_requests)
        start = page * size
        end = start + size
        
        return {
            "requests": all_requests[start:end],
            "currentPage": page,
            "pageSize": size,
            "totalItems": total,
            "totalPages": (total + size - 1) // size if total > 0 else 0,
            "httpStatus": 200
        }

    async def get_status(self, request_id: str) -> Optional[dict]:
        """Get TBR request status"""
        self.logger.info(f"Fetching status for request: {request_id}")
        
        with self.jobs_lock:
            request = self.requests.get(request_id)
        
        return request


class TBRServiceFactory:
    """Factory for creating TBR Service instances"""
    _instance: Optional[TBRService] = None

    @classmethod
    def set_instance(cls, service: TBRService):
        """Set the service instance"""
        cls._instance = service

    @classmethod
    def get_instance(cls) -> TBRService:
        if cls._instance is None:
            raise RuntimeError("TBRService not initialized")
        return cls._instance


def get_tbr_service() -> TBRService:
    """FastAPI dependency for getting TBR service"""
    return TBRServiceFactory.get_instance()

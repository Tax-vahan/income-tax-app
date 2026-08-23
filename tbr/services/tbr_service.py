import os
import json
import threading
import logging
import uuid
from typing import Optional, Dict, List
from datetime import datetime

from tbr.automation.tbr_automation import TBRAutomation
from pan_verification.services.login_service import TracesLoginService

logger = logging.getLogger(__name__)


class TBRService:
    """
    TBR Service — wraps the real TRACES CPCTDS 2.0 Transaction Based Report
    API (see tbr/automation/tbr_automation.py for the captured endpoint
    contracts) behind our own request-tracking layer.

    Every TRACES-facing call requires an authenticated `session_id` obtained
    from pan_verification's TracesLoginService (POST /login/init +
    /login/complete). Local JSON persistence (via tbr_file) is kept purely
    for OUR OWN request bookkeeping — request_id, status, timestamps — so
    the rest of the app (endpoints, polling clients) doesn't need to change
    shape. The `generate_report` / initiate path is best-effort and unverified
    against a live isValid=true response — see tbr_automation.py docstring.
    """

    def __init__(self, tbr_file: str, jobs_lock: threading.Lock, data_dir: str, downloads_dir: str):
        """
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

        self.automation = TBRAutomation()
        self.login_service = TracesLoginService()

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

    async def _quarter_label_to_code(self, session_data: dict, financial_year: str, quarter_label: str) -> str:
        """
        TRACES's TBR API takes a numeric quarter code (e.g. "3" for Q1), not
        the "Q1" label. Resolve it dynamically via getQuarter() rather than
        hardcoding a guessed label->code table, since only Q1=3 was ever
        observed live and the mapping for Q2-Q4 is unconfirmed.
        """
        if quarter_label.isdigit():
            return quarter_label
        quarters = await self.automation.get_quarters(session_data, financial_year)
        for q in quarters:
            if q.get("description", "").upper() == quarter_label.upper():
                return q.get("code")
        raise ValueError(f"Unknown quarter '{quarter_label}' for FY {financial_year}")

    async def get_financial_years(self, session_id: str) -> list:
        session_data = await self.login_service.restore_session(session_id)
        return await self.automation.get_financial_years(session_data)

    async def get_quarters(self, session_id: str, financial_year: str) -> list:
        session_data = await self.login_service.restore_session(session_id)
        return await self.automation.get_quarters(session_data, financial_year)

    async def validate_tan(
        self,
        session_id: str,
        tan: str,
        financial_year: int,
        quarter: str,
    ) -> dict:
        """Validate TAN by calling the real TRACES validate-tan endpoint."""
        self.logger.info(f"Validating TAN: {tan} for FY {financial_year} Q{quarter}")
        session_data = await self.login_service.restore_session(session_id)
        quarter_code = await self._quarter_label_to_code(session_data, str(financial_year), quarter)
        result = await self.automation.validate_tan(session_data, tan, str(financial_year), quarter_code)
        return {
            "isValid": bool(result.get("isValid", False)),
            "message": result.get("message", ""),
            "tan": result.get("tan", tan),
            "finYear": str(result.get("finYear", financial_year)),
            "quarter": int(result.get("quarter", quarter_code)) if str(result.get("quarter", quarter_code)).isdigit() else 0,
            "processingStatus": result.get("processingStatus", ""),
        }

    async def initiate_tbr_request(
        self,
        session_id: str,
        tan: str,
        financial_year: int,
        quarter: str,
        user_id: str,
    ) -> dict:
        """
        Validate TAN first (mirrors the portal's own UI flow — the "Initiate
        Request" button is disabled until validate-tan returns isValid=true),
        then call generate_report(). generate_report() itself is best-effort
        / unverified — see tbr_automation.py.
        """
        request_id = f"TBR_{tan}_{financial_year}_{quarter}_{uuid.uuid4().hex[:8]}"
        self.logger.info(f"Initiating TBR: {request_id}")

        try:
            session_data = await self.login_service.restore_session(session_id)
        except Exception as e:
            self.logger.error(f"Failed to restore session {session_id}: {e}")
            raise ValueError("Invalid or expired session. Please login again.")

        if not session_data or not session_data.get("access_token") or not session_data.get("tan"):
            self.logger.error(f"Session data incomplete for {session_id}")
            raise ValueError("Session invalid. Please login again.")

        quarter_code = await self._quarter_label_to_code(session_data, str(financial_year), quarter)

        validation = await self.automation.validate_tan(session_data, tan, str(financial_year), quarter_code)
        if not validation.get("isValid"):
            request_data = {
                "id": len(self.requests) + 1,
                "request_id": request_id,
                "tan": tan,
                "financial_year": financial_year,
                "quarter": quarter,
                "status": "FAILED",
                "processing_status": validation.get("message", "No statement available for the selected period."),
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

        try:
            generate_result = await self.automation.generate_report(session_data, tan, str(financial_year), quarter_code)
            status = "PROCESSING"
            processing_status = generate_result.get("message", "Report generation queued on TRACES")
        except Exception as e:
            self.logger.error(f"TBR generate_report failed (endpoint unverified): {e}")
            status = "FAILED"
            processing_status = f"TRACES report generation failed: {e}"

        request_data = {
            "id": len(self.requests) + 1,
            "request_id": request_id,
            "tan": tan,
            "financial_year": financial_year,
            "quarter": quarter,
            "status": status,
            "processing_status": processing_status,
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
        session_id: str,
        tan: str,
        page: int = 0,
        size: int = 10,
    ) -> dict:
        """
        Returns TRACES's live ready-download-requests list merged with our
        own locally-tracked request bookkeeping (for request_id lookups via
        get_status()).
        """
        self.logger.info(f"Fetching TBR requests for {tan} (page {page}, size {size})")
        session_data = await self.login_service.restore_session(session_id)
        traces_data = await self.automation.get_ready_download_requests(session_data)

        with self.jobs_lock:
            local_requests = [
                r for r in self.requests.values()
                if r.get("tan") == tan and r.get("status") in ("COMPLETED", "READY")
            ]
        local_requests.sort(key=lambda r: r.get("created_at", ""), reverse=True)

        total = len(local_requests)
        start = page * size
        end = start + size

        return {
            "requests": local_requests[start:end],
            "currentPage": page,
            "pageSize": size,
            "totalItems": total,
            "totalPages": (total + size - 1) // size if total > 0 else 0,
            "httpStatus": 200,
            "traces_live_data": traces_data,
        }

    async def get_status(self, request_id: str) -> Optional[dict]:
        """Get TBR request status (our local bookkeeping record)."""
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

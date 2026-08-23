import json
import os
import threading
from datetime import datetime
from typing import Optional, Dict
from tdstcs.utils.logger import get_logger
from tdstcs.automation.tdstcs_automation import TDSTCSAutomation
from pan_verification.services.login_service import TracesLoginService

logger = get_logger(__name__)


class TDSTCSService:
    """
    TDS/TCS Certificate download service — wraps the real TRACES CPCTDS 2.0
    `tdscertificatesservice` API (see tdstcs/automation/tdstcs_automation.py
    for the captured endpoint contracts) behind our own request-tracking
    layer. Local JSON persistence (via tdstcs_file) is our own bookkeeping;
    TRACES itself is the source of truth for actual request/download status,
    fetched live on every status check.
    """

    def __init__(self, tdstcs_file: str, jobs_lock: threading.Lock, data_dir: str, downloads_dir: str):
        self.tdstcs_file = tdstcs_file
        self.jobs_lock = jobs_lock
        self.data_dir = data_dir
        self.downloads_dir = downloads_dir
        self.requests: Dict = {}
        self._save_pending = False
        self._save_debounce_lock = threading.Lock()
        self._load_requests()

        self.automation = TDSTCSAutomation()
        self.login_service = TracesLoginService()

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

    def _patch_request(self, request_id: str, **fields):
        """Update request with thread safety"""
        with self.jobs_lock:
            if request_id in self.requests:
                self.requests[request_id].update(fields)
                self.requests[request_id]['updated_at'] = datetime.utcnow().isoformat()
        self._save_requests_debounced()

    async def get_form_types(self, session_id: str) -> list:
        """GET form types from TRACES (130/131/133)."""
        session_data = await self.login_service.restore_session(session_id)
        return await self.automation.get_form_types(session_data)

    async def get_financial_years(self, session_id: str) -> list:
        session_data = await self.login_service.restore_session(session_id)
        return await self.automation.get_financial_years(session_data)

    async def get_quarters(self, session_id: str, financial_year: str) -> list:
        session_data = await self.login_service.restore_session(session_id)
        return await self.automation.get_quarters(session_data, financial_year)

    async def initiate_certificate_download(
        self, session_id: str, tan: str, financial_year: str, quarter: str, form_type: str
    ) -> dict:
        """Initiate a TDS/TCS certificate download request against the real TRACES API."""
        import uuid

        try:
            session_data = await self.login_service.restore_session(session_id)
        except Exception as e:
            logger.error(f"Failed to restore session {session_id}: {e}")
            raise ValueError("Invalid or expired session. Please login again.")

        if not session_data or not session_data.get("access_token") or not session_data.get("tan"):
            logger.error(f"Session data incomplete for {session_id}")
            raise ValueError("Session invalid. Please login again.")

        request_id = f"TDSTCS_{tan}_{financial_year.replace('-', '_')}_{quarter}_{uuid.uuid4().hex[:8]}"

        logger.info(
            f"Initiating TDS/TCS download: {request_id} for {tan} ({form_type}, {financial_year}, {quarter})"
        )

        traces_request_id = None
        status = "INITIATED"
        processing_status = "Queued for download"
        try:
            traces_result = await self.automation.initiate(
                session_data, financial_year=financial_year, quarter=quarter, form_type=form_type
            )
            if not isinstance(traces_result, dict):
                logger.error(f"Invalid TRACES response type: {type(traces_result)}")
                raise ValueError("TRACES returned invalid response format")

            if "requestId" not in traces_result:
                logger.error(f"TRACES response missing requestId: {traces_result}")
                raise ValueError("TRACES server error: missing request ID in response")

            traces_request_id = traces_result["requestId"]
            processing_status = "Request submitted to TRACES"
            logger.info(f"TDS/TCS initiate successful: TRACES request ID {traces_request_id}")
        except (ValueError, TypeError) as e:
            logger.error(f"TRACES initiate validation failed for {request_id}: {e}")
            status = "FAILED"
            processing_status = f"TRACES initiate failed: {str(e)}"
        except Exception as e:
            logger.error(f"TRACES initiate error for {request_id}: {e}", exc_info=True)
            status = "FAILED"
            processing_status = f"TRACES initiate failed: {str(e)}"

        request_data = {
            "request_id": request_id,
            "traces_request_id": traces_request_id,
            "tan": tan,
            "financial_year": financial_year,
            "quarter": quarter,
            "form_type": form_type,
            "status": status,
            "processing_status": processing_status,
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
            "status": status,
            "message": processing_status,
            # TRACES returns requestId as a JSON number (confirmed live
            # 2026-08-23, e.g. 250992502) — InitiateTDSTCSResponse declares
            # transaction_id as Optional[str], so cast it or FastAPI's
            # response-model validation rejects the whole response with a
            # 500 even though the TRACES call itself already succeeded.
            "transaction_id": str(traces_request_id) if traces_request_id is not None else None,
        }

    async def get_status(self, session_id: str, request_id: str) -> dict:
        """
        Check status of a certificate download request. Polls TRACES live
        via getActiveRequests()/request-history() (matched by our stored
        traces_request_id) rather than trusting local state, since TRACES
        processing can take anywhere from minutes to hours.
        """
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

        traces_request_id = req.get("traces_request_id")
        if traces_request_id and req.get("status") not in ("COMPLETED", "FAILED"):
            try:
                session_data = await self.login_service.restore_session(session_id)
                active = await self.automation.get_active_requests(session_data, page=0, size=50, fetch_all=True)
                match = next(
                    (r for r in active.get("content", []) if str(r.get("requestId")) == str(traces_request_id)),
                    None,
                )
                if match:
                    # Confirmed live 2026-08-23: getActiveRequests returns
                    # status="Generated" (remarks: "Ready for Download | Link
                    # Valid till ...") once the file can actually be
                    # downloaded via downloadFile — "AVAILABLE"/"READY" were
                    # guesses that never matched a live response. "Downloaded"
                    # covers requests already fetched in a prior session (seen
                    # in request-history) — treat that as done too, not stuck
                    # processing forever.
                    portal_status = (match.get("status") or "").upper()
                    is_ready = portal_status in ("GENERATED", "AVAILABLE", "READY", "COMPLETED", "DOWNLOADED")
                    new_status = "COMPLETED" if is_ready else "PROCESSING"
                    self._patch_request(request_id, status=new_status, processing_status=match.get("remarks", portal_status))
                    req = {**req, "status": new_status, "processing_status": match.get("remarks", portal_status)}
            except Exception as e:
                logger.warning(f"Could not refresh live status for {request_id}: {e}")

        return {
            "request_id": request_id,
            "status": req.get("status", "UNKNOWN"),
            "is_ready": req.get("status") == "COMPLETED",
            "message": req.get("processing_status", ""),
            "progress": 100 if req.get("status") == "COMPLETED" else 50,
            "file_path": req.get("file_path"),
        }

    async def download_certificate(self, session_id: str, request_id: str) -> tuple[bytes, str]:
        """
        Download the certificate file bytes. Requires the request to already
        be COMPLETED (call get_status() first to refresh). The underlying
        TRACES download endpoint itself is unverified — see
        tdstcs_automation.py's download() docstring.
        """
        with self.jobs_lock:
            req = self.requests.get(request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found")

        traces_request_id = req.get("traces_request_id")
        if not traces_request_id:
            raise ValueError(f"Request {request_id} has no TRACES request id to download")

        session_data = await self.login_service.restore_session(session_id)
        file_bytes, filename = await self.automation.download(session_data, traces_request_id)

        self._patch_request(request_id, status="COMPLETED", completed_date=datetime.utcnow().isoformat())
        return file_bytes, filename

    async def list_completed_certificates(
        self, tan: str, page: int = 0, page_size: int = 10
    ) -> dict:
        """List completed certificate downloads for a TAN (our local bookkeeping)."""
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

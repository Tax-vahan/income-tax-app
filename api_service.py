import os
import uuid
import json
import queue
import threading
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from typing import Optional
from pydantic import BaseModel

from fetcher.main import run_fetch
from fetcher.core.api  import fetch_entity_profile
from fetcher.core      import auth as _auth
from fetcher.utils.config import CONFIG as _CFG

from fastapi import Request
from fastapi.responses import JSONResponse
from pan_verification.api.endpoints import router as pan_router
from tbr.api.endpoints import router as tbr_router
from tbr.services.tbr_service import TBRService, TBRServiceFactory
from tdstcs.api.endpoints import router as tdstcs_router
from tdstcs.services.tdstcs_service import TDSTCSService, TDSTCSServiceFactory
from justification.api.endpoints import router as justification_router
from justification.services.justification_service import JustificationService, JustificationServiceFactory
from conso.api.endpoints import router as conso_router
from conso.services.conso_service import ConsoService, ConsoServiceFactory
from pan_verification.automation.browser_manager import BrowserManager
from pan_verification.core import get_session_manager, close_session_manager
from pan_verification.core.page_pool import cleanup_page_pool
from pan_verification.utils.errors import AutomationError, map_automation_error_to_http


BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
DATA_DIR          = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
DOWNLOADS_DIR     = os.environ.get("DOWNLOADS_DIR", os.path.join(BASE_DIR, "downloads"))
FILED_FORMS_DIR   = os.environ.get("FILED_FORMS_DIR", os.path.join(BASE_DIR, "filed_forms"))
JOBS_FILE         = os.path.join(DATA_DIR, "jobs.json")
TBR_REQUESTS_FILE = os.path.join(DATA_DIR, "tbr_requests.json")
TDSTCS_REQUESTS_FILE = os.path.join(DATA_DIR, "tdstcs_requests.json")
JUSTIFICATION_REQUESTS_FILE = os.path.join(DATA_DIR, "justification_requests.json")
CONSO_REQUESTS_FILE = os.path.join(DATA_DIR, "conso_requests.json")

os.makedirs(DATA_DIR,        exist_ok=True)
os.makedirs(DOWNLOADS_DIR,   exist_ok=True)
os.makedirs(FILED_FORMS_DIR, exist_ok=True)


_stop_event = threading.Event()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting TDS background workers...")
    _stop_event.clear()
    
    app.state.jobs = jobs
    app.state.jobs_lock = jobs_lock
    app.state.job_queue = _job_queue
    app.state.stop_event = _stop_event
    
    workers = []
    for _i in range(_WORKER_COUNT):
        t = threading.Thread(
            target=_worker, daemon=True, name=f"JobWorker-{_i + 1}"
        )
        t.start()
        workers.append(t)
    
    app.state.workers = workers
    
    # PAN Verification Startup
    try:
        logger.info("Initializing PAN Verification BrowserManager...")
        await BrowserManager.get_context()
        logger.info("Initializing PAN Verification SessionManager...")
        session_mgr = await get_session_manager()
        
        app.state.browser_manager = BrowserManager
        app.state.session_manager = session_mgr
        app.state.pan_enabled = True
        logger.info("PAN Verification components initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize PAN Verification components: {e}", exc_info=True)
        app.state.pan_enabled = False

    # TBR Service Initialization
    try:
        logger.info("Initializing TBR Service...")
        tbr_service = TBRService(
            tbr_file=TBR_REQUESTS_FILE,
            jobs_lock=tbr_lock,
            data_dir=DATA_DIR,
            downloads_dir=DOWNLOADS_DIR
        )
        TBRServiceFactory.set_instance(tbr_service)
        app.state.tbr_service = tbr_service
        logger.info("TBR Service initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize TBR Service: {e}", exc_info=True)

    # TDS/TCS Service Initialization
    try:
        logger.info("Initializing TDS/TCS Service...")
        tdstcs_service = TDSTCSService(
            tdstcs_file=TDSTCS_REQUESTS_FILE,
            jobs_lock=tdstcs_lock,
            data_dir=DATA_DIR,
            downloads_dir=DOWNLOADS_DIR
        )
        TDSTCSServiceFactory.set_instance(tdstcs_service)
        app.state.tdstcs_service = tdstcs_service
        logger.info("TDS/TCS Service initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize TDS/TCS Service: {e}", exc_info=True)

    # Justification Service Initialization
    try:
        logger.info("Initializing Justification Service...")
        justification_service = JustificationService(
            justification_file=JUSTIFICATION_REQUESTS_FILE,
            jobs_lock=justification_lock,
            data_dir=DATA_DIR,
            downloads_dir=DOWNLOADS_DIR
        )
        JustificationServiceFactory.set_instance(justification_service)
        app.state.justification_service = justification_service
        logger.info("Justification Service initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize Justification Service: {e}")

    # CONSO Service Initialization
    try:
        logger.info("Initializing CONSO Service...")
        conso_service = ConsoService(
            conso_file=CONSO_REQUESTS_FILE,
            jobs_lock=conso_lock,
            data_dir=DATA_DIR,
            downloads_dir=DOWNLOADS_DIR
        )
        ConsoServiceFactory.set_instance(conso_service)
        app.state.conso_service = conso_service
        logger.info("CONSO Service initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize CONSO Service: {e}")

    yield
    
    # Shutdown
    # PAN Verification Cleanup
    if getattr(app.state, "pan_enabled", False):
        try:
            logger.info("Closing PAN Verification BrowserManager...")
            await BrowserManager.close()
            logger.info("Closing PAN Verification SessionManager...")
            await close_session_manager()
            logger.info("Cleaning up PAN Verification page pool...")
            await cleanup_page_pool()
        except Exception as e:
            logger.error(f"Error during PAN Verification cleanup: {e}", exc_info=True)

    # TBR Service Cleanup
    if hasattr(app.state, "tbr_service"):
        try:
            logger.info("Saving TBR requests...")
            app.state.tbr_service._save_requests()
            logger.info("TBR requests saved.")
        except Exception as e:
            logger.error(f"Error saving TBR requests: {e}")

    # TDS/TCS Service Cleanup
    if hasattr(app.state, "tdstcs_service"):
        try:
            logger.info("Saving TDS/TCS requests...")
            app.state.tdstcs_service._save_requests()
            logger.info("TDS/TCS requests saved.")
        except Exception as e:
            logger.error(f"Error saving TDS/TCS requests: {e}")

    # Justification Service Cleanup
    if hasattr(app.state, "justification_service"):
        try:
            logger.info("Saving justification requests...")
            app.state.justification_service._save_requests()
            logger.info("Justification requests saved.")
        except Exception as e:
            logger.error(f"Error saving justification requests: {e}")

    logger.info("Shutting down TDS background workers...")
    _stop_event.set()

    # Push sentinel values to unblock workers waiting on queue.get()
    for _ in range(_WORKER_COUNT):
        _job_queue.put(None)

    for t in workers:
        t.join(timeout=5.0)

    # CONSO Service Cleanup
    if hasattr(app.state, "conso_service"):
        try:
            logger.info("Saving CONSO requests...")
            app.state.conso_service._save_requests()
        except Exception as e:
            logger.error(f"Error saving CONSO: {e}")

    _flush_jobs()
    logger.info("Shutdown complete.")

app = FastAPI(title="TDS Challan API", version="6.0", lifespan=lifespan)


from fastapi import Depends

async def check_pan_enabled(request: Request):
    if getattr(request.app.state, "pan_enabled", False) is False:
        raise HTTPException(status_code=503, detail="PAN Verification service is currently unavailable.")

app.include_router(tbr_router, prefix="/api/v1", tags=["TBR"])
app.include_router(tdstcs_router, prefix="/api/v1", tags=["TDS/TCS Certificates"])
app.include_router(justification_router, prefix="/api/v1", tags=["Justification Report"])
app.include_router(conso_router, prefix="/api/v1", tags=["CONSO File"])
app.include_router(pan_router, prefix="/pan-verification", tags=["PAN Verification"], dependencies=[Depends(check_pan_enabled)])

@app.exception_handler(AutomationError)
async def automation_exception_handler(request: Request, exc: AutomationError):
    logger.error(f"Automation error: {type(exc).__name__} - {str(exc)}")
    http_exc = map_automation_error_to_http(exc)
    return JSONResponse(
        status_code=http_exc.status_code,
        content={"detail": http_exc.detail}
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger("TDS_API")

# ── Concurrency constants ──────────────────────────────────────────────────────
_WORKER_COUNT = 2    # max simultaneous Selenium sessions (Chrome is RAM-heavy)
_MAX_QUEUED   = 10   # reject new jobs with 503 when this many are already waiting

# ── Job store ──────────────────────────────────────────────────────────────────

jobs:      dict  = {}
jobs_lock        = threading.Lock()

# Dedicated locks per module — avoids false contention between unrelated
# subsystems that previously all serialized on the single TDS jobs_lock.
tbr_lock            = threading.Lock()
tdstcs_lock         = threading.Lock()
justification_lock  = threading.Lock()
conso_lock          = threading.Lock()

# Debounced job persistence — coalesce rapid writes into one flush every 2 s
_save_pending = False
_save_lock    = threading.Lock()

if os.path.exists(JOBS_FILE):
    try:
        with open(JOBS_FILE) as f:
            jobs = json.load(f)
    except Exception:
        jobs = {}


def _flush_jobs() -> None:
    with jobs_lock:
        snapshot = dict(jobs)
    try:
        with open(JOBS_FILE, "w") as f:
            json.dump(snapshot, f, indent=2)
    except Exception as exc:
        logger.error("Failed to persist jobs: %s", exc)


def _save_jobs() -> None:
    global _save_pending
    with _save_lock:
        if _save_pending:
            return
        _save_pending = True

    def _do_flush():
        global _save_pending
        _stop_event.wait(timeout=2.0)
        _save_pending = False
        _flush_jobs()

    threading.Thread(target=_do_flush, daemon=True).start()


def _patch_job(job_id: str, **fields) -> None:
    with jobs_lock:
        jobs[job_id].update(fields)
    _save_jobs()


# ── Worker pool ────────────────────────────────────────────────────────────────
# Two long-lived threads drain the queue.  No thread explosion.
# A job sits in queue.Queue while pending — zero CPU cost.

_job_queue: queue.Queue = queue.Queue()


def _worker() -> None:
    """Runs until _stop_event is set; picks (job_id, kind, req) tuples from the queue."""
    while not _stop_event.is_set():
        item = _job_queue.get()
        if item is None:
            _job_queue.task_done()
            break
            
        job_id, kind, req = item
        try:
            if kind == "fetch":
                _run_job(job_id, req)
            elif kind == "entity":
                _run_entity_job(job_id, req)
            elif kind == "view_filed_forms":
                _run_view_filed_forms_job(job_id, req)
            else:
                logger.error("Unknown job kind: %s", kind)
        except Exception:
            logger.exception("Unhandled worker error for job %s", job_id)
        finally:
            _job_queue.task_done()


# ── Queue introspection helpers ────────────────────────────────────────────────

def _active_tan_job(tan: str, kind: str = "fetch"):
    """Return the most recent active (pending/running) job for this TAN, or None."""
    with jobs_lock:
        active = [
            j for j in jobs.values()
            if j.get("tan") == tan
            and j.get("type", "fetch") == kind
            and j.get("status") in ("pending", "running")
        ]
    return active[-1] if active else None


def _pending_count() -> int:
    with jobs_lock:
        return sum(1 for j in jobs.values() if j.get("status") == "pending")


def _queue_position(job_id: str) -> int:
    """1-based position of this job among all pending jobs (by creation time)."""
    with jobs_lock:
        pending = sorted(
            (j for j in jobs.values() if j.get("status") == "pending"),
            key=lambda j: j.get("created_at", ""),
        )
    for i, j in enumerate(pending):
        if j["id"] == job_id:
            return i + 1
    return 0


# ── Models ─────────────────────────────────────────────────────────────────────

class FetchRequest(BaseModel):
    tan:           str
    password:      str
    from_date:     str
    to_date:       str
    financialYear: Optional[str] = None


class EntityRequest(BaseModel):
    tan:      str
    password: str


class ViewFiledFormsRequest(BaseModel):
    tan:          str
    password:     str
    form_type_cd: str


# ── Job runners (called by worker threads, never the request thread) ───────────

def _run_job(job_id: str, req: FetchRequest) -> None:
    logger.info("Job %s started for TAN %s", job_id, req.tan)
    _patch_job(job_id, status="running", started_at=datetime.now().isoformat())

    job_dir     = os.path.join(DOWNLOADS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    output_file = os.path.join(job_dir, f"TDS_Challans_{req.tan}.xlsx")

    cfg = {
        "TAN":            req.tan,
        "PASSWORD":       req.password,
        "FROM_DATE":      req.from_date,
        "TO_DATE":        req.to_date,
        "FINANCIAL_YEAR": req.financialYear,
        "OUTPUT_PATH":    output_file,
        "DOWNLOAD_DIR":   job_dir,
    }

    try:
        path, count, grand = run_fetch(cfg)
        entity_path = os.path.join(DATA_DIR, f"{req.tan}_entity_profile.json")
        json_path   = path.rsplit(".", 1)[0] + ".json"
        _patch_job(
            job_id,
            status="completed",
            completed_at=datetime.now().isoformat(),
            result={
                "excel_file":          path,
                "json_file":           json_path if os.path.exists(json_path) else None,
                "record_count":        count,
                "grand_total":         grand,
                "entity_profile_file": entity_path if os.path.exists(entity_path) else None,
            },
        )
        logger.info("Job %s done: %d records, total %s",
                    job_id, count, f"{grand:,.0f}")
    except Exception:
        logger.exception("Job %s failed", job_id)
        _patch_job(job_id, status="failed",
                   completed_at=datetime.now().isoformat())


def _run_entity_job(job_id: str, req: EntityRequest) -> None:
    logger.info("Entity job %s started for TAN %s", job_id, req.tan)
    _patch_job(job_id, status="running", started_at=datetime.now().isoformat())
    cfg = {**_CFG, "TAN": req.tan, "PASSWORD": req.password}
    try:
        session, driver, cdp_capture = _auth.get_session(cfg)
        if session is None:
            raise RuntimeError("Session establishment failed")

        from fetcher.main import _save_entity_profile

        profile = None
        if cdp_capture is not None and driver is not None:
            profile = cdp_capture.get_response_body("/servicesapi/auth/saveEntity", driver, timeout=15)
            if profile and "orgName" not in profile:
                profile = None
        if profile is None:
            profile = fetch_entity_profile(session, req.tan)
        if profile is None or "orgName" not in profile:
            raise RuntimeError("Could not retrieve entity profile via CDP or API")

        _save_entity_profile(profile, cfg)

        if cdp_capture:
            cdp_capture.stop()
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

        profile_path = os.path.join(DATA_DIR, f"{req.tan}_entity_profile.json")
        _patch_job(
            job_id,
            status="completed",
            completed_at=datetime.now().isoformat(),
            result={"entity_profile_file": profile_path},
        )
        logger.info("Entity job %s done — %s", job_id, profile_path)
    except Exception:
        logger.exception("Entity job %s failed", job_id)
        _patch_job(job_id, status="failed",
                   completed_at=datetime.now().isoformat())


def _run_view_filed_forms_job(job_id: str, req: ViewFiledFormsRequest) -> None:
    logger.info("ViewFiledForms job %s started for TAN %s, Form %s", job_id, req.tan, req.form_type_cd)
    _patch_job(job_id, status="running", started_at=datetime.now().isoformat())
    cfg = {**_CFG, "TAN": req.tan, "PASSWORD": req.password}
    try:
        session, driver, cdp_capture = _auth.get_session(cfg)
        if session is None:
            raise RuntimeError("Session establishment failed")

        from fetcher.core.api import fetch_view_filed_forms
        resp = fetch_view_filed_forms(session, req.tan, req.form_type_cd)
        
        forms_data = resp.get("forms", [])
        extracted = []
        for form in forms_data:
            extracted.append({
                "tempAckNo": form.get("tempAckNo"),
                "ackDt": form.get("ackDt"),
                "ackNum": form.get("ackNum"),
                "filingTypeCd": form.get("filingTypeCd"),
                "fillingMode": form.get("fillingMode"),
                "activities": form.get("activities"),
                "financialQrtr": form.get("financialQrtr"),
                "formTypeCd": form.get("formTypeCd"),
                "refYear": form.get("refYear"),
            })

        if cdp_capture:
            cdp_capture.stop()
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

        # Save to filed_forms/<TAN>/<form_type>/<TAN>_<form_type>.json
        form_dir = os.path.join(FILED_FORMS_DIR, req.tan, req.form_type_cd)
        os.makedirs(form_dir, exist_ok=True)
        out_path = os.path.join(form_dir, f"{req.tan}_{req.form_type_cd}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(extracted, f, indent=2)

        _patch_job(
            job_id,
            status="completed",
            completed_at=datetime.now().isoformat(),
            result={"json_file": out_path},
        )
        logger.info("ViewFiledForms job %s done — %s", job_id, out_path)
    except Exception:
        logger.exception("ViewFiledForms job %s failed", job_id)
        _patch_job(job_id, status="failed",
                   completed_at=datetime.now().isoformat())


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.post("/tds/api/v1/fetch")
async def create_fetch_job(req: FetchRequest):
    # Deduplicate: if this TAN is already active, return the existing job
    existing = _active_tan_job(req.tan, "fetch")
    if existing:
        return {
            "job_id":  existing["id"],
            "status":  existing["status"],
            "message": (
                f"A fetch job for TAN {req.tan} is already "
                f"{existing['status']}. Poll this job_id for updates."
            ),
        }

    pending = _pending_count()
    if pending >= _MAX_QUEUED:
        raise HTTPException(
            status_code=503,
            detail=f"Server busy — {pending} jobs queued. Try again in a few minutes.",
        )

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "id":         job_id,
            "tan":        req.tan,
            "type":       "fetch",
            "status":     "pending",
            "created_at": datetime.now().isoformat(),
        }
    _save_jobs()
    _job_queue.put((job_id, "fetch", req))
    pos = _queue_position(job_id)
    return {"job_id": job_id, "status": "pending", "queue_position": pos}


@app.get("/tds/api/v1/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = dict(jobs[job_id])
    if job["status"] == "pending":
        job["queue_position"] = _queue_position(job_id)
    return job


from fastapi import WebSocket, WebSocketDisconnect
import asyncio

@app.websocket("/tds/api/v1/jobs/{job_id}/ws")
async def job_websocket(websocket: WebSocket, job_id: str):
    """
    WebSocket endpoint for the frontend to receive real-time updates on a job's status.
    The frontend won't have to guess or spam HTTP polling requests.
    """
    await websocket.accept()
    try:
        while True:
            if job_id not in jobs:
                await websocket.send_json({"status": "not_found", "message": "Job not found"})
                break
            
            job = jobs[job_id]
            msg = {"status": job["status"]}
            if job["status"] == "pending":
                msg["queue_position"] = _queue_position(job_id)
            elif job["status"] == "completed":
                msg["result"] = job.get("result", {})
            elif job["status"] == "failed":
                msg["error"] = "Job failed during execution."
                
            await websocket.send_json(msg)
            
            if job["status"] in ["completed", "failed"]:
                break
                
            # Check for updates every 2 seconds
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for job {job_id}")



@app.get("/tds/api/v1/jobs/{job_id}/download")
async def download_result(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    if job["status"] != "completed":
        raise HTTPException(status_code=400,
                            detail=f"Job status is '{job['status']}'")
    path = job.get("result", {}).get("excel_file")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Result file not found")
    return FileResponse(path, filename=os.path.basename(path))


@app.get("/tds/api/v1/jobs/{job_id}/data")
async def get_job_data(job_id: str):
    """Return the enriched challan array as JSON."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    if job["status"] != "completed":
        raise HTTPException(status_code=400,
                            detail=f"Job status is '{job['status']}'")
    json_path = job.get("result", {}).get("json_file")
    if not json_path or not os.path.exists(json_path):
        raise HTTPException(status_code=404,
                            detail="JSON data file not found — re-run the job")
    try:
        with open(json_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        raise HTTPException(status_code=500,
                            detail=f"Failed to read data file: {exc}")


@app.get("/tds/api/v1/jobs")
async def list_jobs():
    with jobs_lock:
        return list(jobs.values())


@app.get("/tds/api/v1/queue")
async def queue_status():
    """Real-time view of running and pending jobs."""
    with jobs_lock:
        all_jobs = list(jobs.values())

    running = [j for j in all_jobs if j.get("status") == "running"]
    pending = sorted(
        [j for j in all_jobs if j.get("status") == "pending"],
        key=lambda j: j.get("created_at", ""),
    )
    return {
        "workers":            _WORKER_COUNT,
        "running":            len(running),
        "pending":            len(pending),
        "capacity_remaining": max(0, _MAX_QUEUED - len(pending)),
        "running_jobs": [
            {
                "job_id":     j["id"],
                "tan":        j["tan"],
                "started_at": j.get("started_at"),
            }
            for j in running
        ],
        "queued_jobs": [
            {
                "job_id":         j["id"],
                "tan":            j["tan"],
                "queue_position": i + 1,
                "waiting_since":  j.get("created_at"),
            }
            for i, j in enumerate(pending)
        ],
    }


@app.post("/tds/api/v1/entity")
async def fetch_entity(req: EntityRequest):
    """Fetch and cache the deductor entity profile."""
    existing = _active_tan_job(req.tan, "entity")
    if existing:
        return {
            "job_id":  existing["id"],
            "status":  existing["status"],
            "message": f"An entity job for TAN {req.tan} is already {existing['status']}.",
        }

    # Optimization: if the profile already exists and is fresh (< 7 days), don't fetch it again
    profile_path = os.path.join(DATA_DIR, f"{req.tan}_entity_profile.json")
    if os.path.exists(profile_path):
        import time
        if time.time() - os.path.getmtime(profile_path) < 7 * 86400:
            job_id = str(uuid.uuid4())
            with jobs_lock:
                jobs[job_id] = {
                    "id": job_id,
                    "tan": req.tan,
                    "type": "entity",
                    "status": "completed",
                    "created_at": datetime.now().isoformat(),
                    "started_at": datetime.now().isoformat(),
                    "completed_at": datetime.now().isoformat(),
                    "result": {"json_file": profile_path}
                }
            _save_jobs()
            return {"job_id": job_id, "status": "completed", "queue_position": 0}

    pending = _pending_count()
    if pending >= _MAX_QUEUED:
        raise HTTPException(
            status_code=503,
            detail=f"Server busy — {pending} jobs queued. Try again later.",
        )

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "id":         job_id,
            "tan":        req.tan,
            "type":       "entity",
            "status":     "pending",
            "created_at": datetime.now().isoformat(),
        }
    _save_jobs()
    _job_queue.put((job_id, "entity", req))
    pos = _queue_position(job_id)
    return {"job_id": job_id, "status": "pending", "queue_position": pos}


@app.get("/tds/api/v1/entity/{tan}")
async def get_entity_profile(tan: str):
    """Return the cached entity profile for a TAN."""
    profile_path = os.path.join(DATA_DIR, f"{tan}_entity_profile.json")
    if not os.path.exists(profile_path):
        raise HTTPException(
            status_code=404,
            detail=(
                f"No entity profile found for TAN {tan}. "
                "Run POST /tds/api/v1/entity or POST /tds/api/v1/fetch first."
            ),
        )
    try:
        with open(profile_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        raise HTTPException(status_code=500,
                            detail=f"Failed to read profile: {exc}")


@app.post("/tds/api/v1/view-filed-forms")
async def view_filed_forms(req: ViewFiledFormsRequest):
    """Fetch filed forms for a given form type (e.g. F27EQ)."""
    existing = _active_tan_job(req.tan, "view_filed_forms")
    if existing:
        return {
            "job_id":  existing["id"],
            "status":  existing["status"],
            "message": f"A view_filed_forms job for TAN {req.tan} is already {existing['status']}.",
        }

    pending = _pending_count()
    if pending >= _MAX_QUEUED:
        raise HTTPException(
            status_code=503,
            detail=f"Server busy — {pending} jobs queued. Try again later.",
        )

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "id":         job_id,
            "tan":        req.tan,
            "form_type":  req.form_type_cd,
            "type":       "view_filed_forms",
            "status":     "pending",
            "created_at": datetime.now().isoformat(),
        }
    _save_jobs()
    _job_queue.put((job_id, "view_filed_forms", req))
    pos = _queue_position(job_id)
    return {"job_id": job_id, "status": "pending", "queue_position": pos}


@app.get("/tds/api/v1/view-filed-forms/{tan}/{form_type_cd}")
async def get_filed_forms(tan: str, form_type_cd: str):
    """Return the cached filed forms data for a TAN and form type.

    Example: GET /tds/api/v1/view-filed-forms/PTLA13241E/F27EQ
    """
    form_path = os.path.join(FILED_FORMS_DIR, tan, form_type_cd, f"{tan}_{form_type_cd}.json")
    if not os.path.exists(form_path):
        raise HTTPException(
            status_code=404,
            detail=(
                f"No filed forms found for TAN {tan}, form {form_type_cd}. "
                "Run POST /tds/api/v1/view-filed-forms first to fetch and cache the data."
            ),
        )
    try:
        with open(form_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        raise HTTPException(status_code=500,
                            detail=f"Failed to read filed forms data: {exc}")


@app.get("/health", tags=["Monitoring"])
async def root_health(request: Request):
    pan_enabled = getattr(request.app.state, "pan_enabled", False)
    return {
        "status": "healthy" if pan_enabled else "degraded",
        "services": {
            "tds": "healthy",
            "pan_verification": "healthy" if pan_enabled else "unavailable"
        }
    }

@app.get("/tds/api/health")
async def health():
    with jobs_lock:
        all_jobs = list(jobs.values())
    return {
        "status":    "ok",
        "timestamp": datetime.now().isoformat(),
        "workers":   _WORKER_COUNT,
        "running":   sum(1 for j in all_jobs if j.get("status") == "running"),
        "pending":   sum(1 for j in all_jobs if j.get("status") == "pending"),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

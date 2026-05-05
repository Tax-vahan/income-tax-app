import os
import uuid
import json
import threading
import logging
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from fetcher.main import run_fetch
from fetcher.core.api  import fetch_entity_profile
from fetcher.core      import auth as _auth
from fetcher.utils.config import CONFIG as _CFG

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR      = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
JOBS_FILE     = os.path.join(DATA_DIR, "jobs.json")

os.makedirs(DATA_DIR,      exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)


app = FastAPI(title="TDS Challan API", version="6.0")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger("TDS_API")

# ── Job store ─────────────────────────────────────────────────────────────────

jobs:      dict  = {}
jobs_lock        = threading.Lock()
_sem             = threading.Semaphore(2)   # max 2 concurrent Selenium instances

# Debounced job persistence — write at most once per 3 seconds
_save_pending  = False
_save_lock     = threading.Lock()

if os.path.exists(JOBS_FILE):
    try:
        with open(JOBS_FILE) as f:
            jobs = json.load(f)
    except Exception:
        jobs = {}


def _flush_jobs() -> None:
    """Write jobs dict to disk (called from background debounce thread)."""
    with jobs_lock:
        snapshot = dict(jobs)
    try:
        with open(JOBS_FILE, "w") as f:
            json.dump(snapshot, f, indent=2)
    except Exception as exc:
        logger.error("Failed to persist jobs: %s", exc)


def _save_jobs() -> None:
    """Schedule a debounced flush — coalesces rapid successive writes."""
    global _save_pending
    with _save_lock:
        if _save_pending:
            return
        _save_pending = True

    def _do_flush():
        global _save_pending
        # Wait a moment to absorb any following writes in the same burst
        threading.Event().wait(timeout=2.0)
        _save_pending = False
        _flush_jobs()

    threading.Thread(target=_do_flush, daemon=True).start()


def _patch_job(job_id: str, **fields) -> None:
    with jobs_lock:
        jobs[job_id].update(fields)
    _save_jobs()


# ── Models ────────────────────────────────────────────────────────────────────

class FetchRequest(BaseModel):
    tan:       str
    password:  str
    from_date: str
    to_date:   str


class EntityRequest(BaseModel):
    tan:      str
    password: str


# ── Background worker ─────────────────────────────────────────────────────────

def _run_job(job_id: str, req: FetchRequest) -> None:
    logger.info("Job %s queued — waiting for concurrency slot ...", job_id)

    with _sem:
        logger.info("Job %s started for TAN %s", job_id, req.tan)
        _patch_job(job_id, status="running",
                   started_at=datetime.now().isoformat())

        job_dir     = os.path.join(DOWNLOADS_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        output_file = os.path.join(job_dir, f"TDS_Challans_{req.tan}.xlsx")

        cfg = {
            "TAN":          req.tan,
            "PASSWORD":     req.password,
            "FROM_DATE":    req.from_date,
            "TO_DATE":      req.to_date,
            "OUTPUT_PATH":  output_file,
            "DOWNLOAD_DIR": job_dir,
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
            _patch_job(
                job_id,
                status="failed",
                completed_at=datetime.now().isoformat(),
            )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/tds/api/v1/fetch")
async def create_fetch_job(req: FetchRequest,
                           background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "id":         job_id,
            "tan":        req.tan,
            "status":     "pending",
            "created_at": datetime.now().isoformat(),
        }
    _save_jobs()
    background_tasks.add_task(_run_job, job_id, req)
    return {"job_id": job_id, "status": "pending"}


@app.get("/tds/api/v1/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]


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
    """
    Return the enriched challan array as JSON — no Excel download needed.

    The JSON sidecar is written alongside the .xlsx during job execution.
    Response is the same data the Excel contains, as a plain JSON array.
    """
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
    return list(jobs.values())


@app.post("/tds/api/v1/entity")
async def fetch_entity(req: EntityRequest, background_tasks: BackgroundTasks):
    """
    Fetch the deductor entity profile from the ITD portal and return it.

    Flow
    ----
    1. Reuse a saved session if one exists for the TAN (fast — no Selenium).
    2. Otherwise start a fresh Selenium login (takes ~30-60 s).
    3. Call /servicesapi/auth/saveEntity and return the profile as JSON.
    """
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
    background_tasks.add_task(_run_entity_job, job_id, req)
    return {"job_id": job_id, "status": "pending"}


def _run_entity_job(job_id: str, req: EntityRequest) -> None:
    logger.info("Entity job %s started for TAN %s", job_id, req.tan)
    with _sem:
        _patch_job(job_id, status="running",
                   started_at=datetime.now().isoformat())
        cfg = {
            **_CFG,
            "TAN":      req.tan,
            "PASSWORD": req.password,
        }
        try:
            session, driver, cdp_capture = _auth.get_session(cfg)
            if session is None:
                raise RuntimeError("Session establishment failed")

            from fetcher.main import _save_entity_profile

            profile = None
            if cdp_capture is not None and driver is not None:
                profile = cdp_capture.get_response_body(
                    "/saveEntity", driver, timeout=10
                )
            if profile is None:
                profile = fetch_entity_profile(session, req.tan)
            if profile is None:
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


@app.get("/tds/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

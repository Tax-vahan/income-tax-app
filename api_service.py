import os
import uuid
import json
import queue
import threading
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException
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

# ── Concurrency constants ──────────────────────────────────────────────────────
_WORKER_COUNT = 2    # max simultaneous Selenium sessions (Chrome is RAM-heavy)
_MAX_QUEUED   = 10   # reject new jobs with 503 when this many are already waiting

# ── Job store ──────────────────────────────────────────────────────────────────

jobs:      dict  = {}
jobs_lock        = threading.Lock()

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
        threading.Event().wait(timeout=2.0)
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
    """Runs forever; picks (job_id, kind, req) tuples from the queue."""
    while True:
        job_id, kind, req = _job_queue.get()
        try:
            if kind == "fetch":
                _run_job(job_id, req)
            else:
                _run_entity_job(job_id, req)
        except Exception:
            logger.exception("Unhandled worker error for job %s", job_id)
        finally:
            _job_queue.task_done()


for _i in range(_WORKER_COUNT):
    threading.Thread(
        target=_worker, daemon=True, name=f"JobWorker-{_i + 1}"
    ).start()


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
    tan:       str
    password:  str
    from_date: str
    to_date:   str


class EntityRequest(BaseModel):
    tan:      str
    password: str


# ── Job runners (called by worker threads, never the request thread) ───────────

def _run_job(job_id: str, req: FetchRequest) -> None:
    logger.info("Job %s started for TAN %s", job_id, req.tan)
    _patch_job(job_id, status="running", started_at=datetime.now().isoformat())

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
            profile = cdp_capture.get_response_body("/saveEntity", driver, timeout=10)
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

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

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
JOBS_FILE     = os.path.join(BASE_DIR, "jobs.json")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)

app = FastAPI(title="TDS Challan API", version="6.0")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger("TDS_API")

# ── Job store ─────────────────────────────────────────────────────────────────

jobs: dict = {}
jobs_lock  = threading.Lock()
# Allow at most 2 concurrent Selenium instances (each needs ~1 GB RAM)
_sem       = threading.Semaphore(2)

if os.path.exists(JOBS_FILE):
    try:
        with open(JOBS_FILE) as f:
            jobs = json.load(f)
    except Exception:
        jobs = {}


def _save_jobs() -> None:
    with jobs_lock:
        try:
            with open(JOBS_FILE, "w") as f:
                json.dump(jobs, f, indent=2)
        except Exception as exc:
            logger.error("Failed to persist jobs: %s", exc)


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
            _patch_job(
                job_id,
                status="completed",
                completed_at=datetime.now().isoformat(),
                result={
                    "excel_file":   path,
                    "record_count": count,
                    "grand_total":  grand,
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


@app.get("/tds/api/v1/jobs")
async def list_jobs():
    return list(jobs.values())


@app.get("/tds/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

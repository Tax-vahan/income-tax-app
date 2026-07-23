from fastapi.testclient import TestClient

from api_service import app, jobs, jobs_lock

client = TestClient(app)


def _seed_jobs():
    with jobs_lock:
        jobs.clear()
        jobs["job-a"] = {
            "id": "job-a", "tan": "USERA1234A", "type": "verify",
            "status": "completed", "created_at": "2026-07-23T00:00:00",
            "result": {"totalManual": 3, "verified": 3, "details": [{"voucherNo": "secret-a"}]},
        }
        jobs["job-b"] = {
            "id": "job-b", "tan": "USERB5678B", "type": "fetch",
            "status": "running", "created_at": "2026-07-23T00:01:00",
            "started_at": "2026-07-23T00:01:00",
        }


def test_jobs_listing_endpoint_is_removed():
    _seed_jobs()
    resp = client.get("/tds/api/v1/jobs")
    assert resp.status_code == 404


def test_queue_status_reports_only_aggregate_counts_no_per_job_data():
    _seed_jobs()
    resp = client.get("/tds/api/v1/queue")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body.keys()) == {"workers", "running", "pending", "capacity_remaining"}
    assert body["running"] == 1
    assert body["pending"] == 0

    body_str = str(body)
    assert "USERA1234A" not in body_str
    assert "USERB5678B" not in body_str
    assert "job-a" not in body_str
    assert "job-b" not in body_str
    assert "secret-a" not in body_str


def test_job_id_scoped_endpoints_still_work():
    _seed_jobs()
    resp = client.get("/tds/api/v1/jobs/job-a")
    assert resp.status_code == 200
    assert resp.json()["result"]["totalManual"] == 3

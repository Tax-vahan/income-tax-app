from unittest.mock import patch

from fastapi.testclient import TestClient

import api_service
from api_service import app, jobs, jobs_lock, VerifyChallansRequest

client = TestClient(app)


def _manual_item(id_="m1"):
    return {
        "id":            id_,
        "voucherNo":     "37358",
        "bsrCode":       "0180002",
        "dateOfDeposit": "07/05/2026",
        "totalAmount":   52830.00,
    }


def _clear_jobs():
    with jobs_lock:
        jobs.clear()


def test_empty_manual_challans_returns_synchronous_200():
    _clear_jobs()
    resp = client.post("/tds/api/v1/challan/verify", json={
        "tan": "TESTTAN1234A", "password": "x", "manual_challans": [],
    })
    assert resp.status_code == 200
    assert resp.json() == {
        "success": True, "message": "No manual challans found.",
        "totalManual": 0, "verified": 0, "notVerified": 0, "details": [],
    }
    with jobs_lock:
        assert jobs == {}


def test_unparseable_date_returns_422():
    _clear_jobs()
    resp = client.post("/tds/api/v1/challan/verify", json={
        "tan": "TESTTAN1234A", "password": "x",
        "manual_challans": [{**_manual_item(), "dateOfDeposit": "not-a-date"}],
    })
    assert resp.status_code == 422
    assert "Unparseable dateOfDeposit" in resp.json()["detail"]
    with jobs_lock:
        assert jobs == {}


def test_dedups_against_active_fetch_job_for_same_tan():
    _clear_jobs()
    with jobs_lock:
        jobs["existing-job"] = {
            "id": "existing-job", "tan": "TESTTAN1234A", "type": "fetch",
            "status": "running", "created_at": "2026-07-22T00:00:00",
        }
    resp = client.post("/tds/api/v1/challan/verify", json={
        "tan": "TESTTAN1234A", "password": "x", "manual_challans": [_manual_item()],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "existing-job"
    assert body["status"] == "running"


def test_run_verify_job_marks_failed_on_fetch_error(tmp_path, monkeypatch):
    monkeypatch.setattr(api_service, "DOWNLOADS_DIR", str(tmp_path))
    job_id = "job-fail"
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id, "tan": "TESTTAN1234A", "type": "verify",
            "status": "pending", "created_at": "2026-07-22T00:00:00",
        }
    req = VerifyChallansRequest(
        tan="TESTTAN1234A", password="x", manual_challans=[_manual_item()],
    )

    with patch("api_service.run_fetch", side_effect=RuntimeError("TRACES login failed")):
        api_service._run_verify_job(job_id, req)

    with jobs_lock:
        job = jobs[job_id]
    assert job["status"] == "failed"
    assert job["error"] == "Unable to fetch challans from Income Tax portal."


def test_run_verify_job_completes_with_empty_portal_response(tmp_path, monkeypatch):
    monkeypatch.setattr(api_service, "DOWNLOADS_DIR", str(tmp_path))
    job_id = "job-empty"
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id, "tan": "TESTTAN1234A", "type": "verify",
            "status": "pending", "created_at": "2026-07-22T00:00:00",
        }
    req = VerifyChallansRequest(
        tan="TESTTAN1234A", password="x", manual_challans=[_manual_item()],
    )

    xlsx_path = tmp_path / "fake.xlsx"
    json_path = tmp_path / "fake.json"
    json_path.write_text("[]")

    with patch("api_service.run_fetch", return_value=(str(xlsx_path), 0, 0)):
        api_service._run_verify_job(job_id, req)

    with jobs_lock:
        job = jobs[job_id]
    assert job["status"] == "completed"
    assert job["result"]["message"] == "No challans found on the government portal for the selected date range."
    assert job["result"]["verified"] == 0
    assert job["result"]["notVerified"] == 1


def test_run_verify_job_matches_a_challan(tmp_path, monkeypatch):
    monkeypatch.setattr(api_service, "DOWNLOADS_DIR", str(tmp_path))
    job_id = "job-match"
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id, "tan": "TESTTAN1234A", "type": "verify",
            "status": "pending", "created_at": "2026-07-22T00:00:00",
        }
    req = VerifyChallansRequest(
        tan="TESTTAN1234A", password="x", manual_challans=[_manual_item()],
    )

    xlsx_path = tmp_path / "fake.xlsx"
    json_path = tmp_path / "fake.json"
    json_path.write_text(
        '[{"bsrCode": "0180002", "challanNum": "37358", "tenderDt": "07/05/2026", '
        '"totalAmt": 52830, "crn": "26050700123452KKBK"}]'
    )

    with patch("api_service.run_fetch", return_value=(str(xlsx_path), 1, 52830)):
        api_service._run_verify_job(job_id, req)

    with jobs_lock:
        job = jobs[job_id]
    assert job["status"] == "completed"
    assert job["result"]["verified"] == 1
    assert job["result"]["details"][0]["matchedCrn"] == "26050700123452KKBK"

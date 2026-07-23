from unittest.mock import patch

from fastapi.testclient import TestClient

import api_service
from api_service import app, jobs, jobs_lock, VerifyChallansRequest

client = TestClient(app)


def _verify_body(**overrides):
    body = {
        "tan":           "TESTTAN1234A",
        "password":      "x",
        "deductorId":    "404868",
        "financialYear": "2026-27",
        "quarter":       "Q1",
        "categoryId":    2,
        "authToken":     "Bearer test-caller-token",
    }
    body.update(overrides)
    return body


def _main_api_challan(id_=3705, voucher="37358", bsr="0180002", date="07/05/2026",
                       amount=52830.00, section_code="", book_entry="N"):
    return {
        "id": id_, "challanVoucherNo": voucher, "bsrCode": bsr,
        "dateOfDeposit": date, "totalTaxDeposit": amount,
        "sectionCode": section_code, "tdsDepositByBook": book_entry,
    }


def _clear_jobs():
    with jobs_lock:
        jobs.clear()


def test_dedups_against_active_fetch_job_for_same_tan():
    _clear_jobs()
    with jobs_lock:
        jobs["existing-job"] = {
            "id": "existing-job", "tan": "TESTTAN1234A", "type": "fetch",
            "status": "running", "created_at": "2026-07-22T00:00:00",
        }
    resp = client.post("/tds/api/v1/challan/verify", json=_verify_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "existing-job"
    assert body["status"] == "running"


def test_creates_pending_job_for_new_tan():
    _clear_jobs()
    resp = client.post("/tds/api/v1/challan/verify", json=_verify_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert "job_id" in body
    with jobs_lock:
        job = jobs[body["job_id"]]
    assert job["type"] == "verify"
    assert job["tan"] == "TESTTAN1234A"


def test_run_verify_job_fails_when_main_api_fetch_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(api_service, "DOWNLOADS_DIR", str(tmp_path))
    job_id = "job-main-api-fail"
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id, "tan": "TESTTAN1234A", "type": "verify",
            "status": "pending", "created_at": "2026-07-22T00:00:00",
        }
    req = VerifyChallansRequest(**_verify_body())

    with patch("api_service.taxvahan_api.fetch_manual_challans",
               side_effect=RuntimeError("unauthorized")) as mock_fetch_manual, \
         patch("api_service.run_fetch") as mock_run_fetch:
        api_service._run_verify_job(job_id, req)

    mock_run_fetch.assert_not_called()
    with jobs_lock:
        job = jobs[job_id]
    assert job["status"] == "failed"
    assert job["error"] == "Unable to fetch manual challans from TaxVahan."


def test_run_verify_job_completes_when_no_eligible_manual_challans(tmp_path, monkeypatch):
    monkeypatch.setattr(api_service, "DOWNLOADS_DIR", str(tmp_path))
    job_id = "job-no-eligible"
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id, "tan": "TESTTAN1234A", "type": "verify",
            "status": "pending", "created_at": "2026-07-22T00:00:00",
        }
    req = VerifyChallansRequest(**_verify_body())

    # SectionCode is set, so nothing here is eligible for verification.
    raw = [_main_api_challan(section_code="194Q")]

    with patch("api_service.taxvahan_api.fetch_manual_challans", return_value=raw), \
         patch("api_service.run_fetch") as mock_run_fetch:
        api_service._run_verify_job(job_id, req)

    mock_run_fetch.assert_not_called()
    with jobs_lock:
        job = jobs[job_id]
    assert job["status"] == "completed"
    assert job["result"]["message"] == "No manual challans found."
    assert job["result"]["totalManual"] == 0


def test_run_verify_job_marks_failed_on_traces_fetch_error(tmp_path, monkeypatch):
    monkeypatch.setattr(api_service, "DOWNLOADS_DIR", str(tmp_path))
    job_id = "job-fail"
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id, "tan": "TESTTAN1234A", "type": "verify",
            "status": "pending", "created_at": "2026-07-22T00:00:00",
        }
    req = VerifyChallansRequest(**_verify_body())

    with patch("api_service.taxvahan_api.fetch_manual_challans", return_value=[_main_api_challan()]), \
         patch("api_service.run_fetch", side_effect=RuntimeError("TRACES login failed")):
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
    req = VerifyChallansRequest(**_verify_body())

    xlsx_path = tmp_path / "fake.xlsx"
    json_path = tmp_path / "fake.json"
    json_path.write_text("[]")

    with patch("api_service.taxvahan_api.fetch_manual_challans", return_value=[_main_api_challan()]), \
         patch("api_service.run_fetch", return_value=(str(xlsx_path), 0, 0)):
        api_service._run_verify_job(job_id, req)

    with jobs_lock:
        job = jobs[job_id]
    assert job["status"] == "completed"
    assert job["result"]["message"] == "No challans found on the government portal for the selected date range."
    assert job["result"]["verified"] == 0
    assert job["result"]["notFound"] == 1
    assert job["result"]["amountNotVerified"] == 0
    assert job["result"]["notVerified"] == 1
    assert job["result"]["details"][0]["status"] == "Not Found"


def test_run_verify_job_marks_amount_not_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(api_service, "DOWNLOADS_DIR", str(tmp_path))
    job_id = "job-amount-mismatch"
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id, "tan": "TESTTAN1234A", "type": "verify",
            "status": "pending", "created_at": "2026-07-22T00:00:00",
        }
    req = VerifyChallansRequest(**_verify_body())

    xlsx_path = tmp_path / "fake.xlsx"
    json_path = tmp_path / "fake.json"
    # Same BSR/voucher/date as _main_api_challan()'s eligible mapping, different amount.
    json_path.write_text(
        '[{"bsrCode": "0180002", "challanNum": "37358", "tenderDt": "07/05/2026", '
        '"totalAmt": 99999, "crn": "26050700123452KKBK"}]'
    )

    with patch("api_service.taxvahan_api.fetch_manual_challans", return_value=[_main_api_challan()]), \
         patch("api_service.run_fetch", return_value=(str(xlsx_path), 1, 99999)):
        api_service._run_verify_job(job_id, req)

    with jobs_lock:
        job = jobs[job_id]
    assert job["status"] == "completed"
    assert job["result"]["verified"] == 0
    assert job["result"]["amountNotVerified"] == 1
    assert job["result"]["notFound"] == 0
    assert job["result"]["details"][0]["status"] == "Amount Not Verified"
    assert job["result"]["details"][0]["matchedCrn"] == "26050700123452KKBK"


def test_run_verify_job_matches_a_challan(tmp_path, monkeypatch):
    monkeypatch.setattr(api_service, "DOWNLOADS_DIR", str(tmp_path))
    job_id = "job-match"
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id, "tan": "TESTTAN1234A", "type": "verify",
            "status": "pending", "created_at": "2026-07-22T00:00:00",
        }
    req = VerifyChallansRequest(**_verify_body())

    xlsx_path = tmp_path / "fake.xlsx"
    json_path = tmp_path / "fake.json"
    json_path.write_text(
        '[{"bsrCode": "0180002", "challanNum": "37358", "tenderDt": "07/05/2026", '
        '"totalAmt": 52830, "crn": "26050700123452KKBK"}]'
    )

    with patch("api_service.taxvahan_api.fetch_manual_challans", return_value=[_main_api_challan()]) as mock_fetch_manual, \
         patch("api_service.run_fetch", return_value=(str(xlsx_path), 1, 52830)):
        api_service._run_verify_job(job_id, req)

    assert mock_fetch_manual.call_args.kwargs["auth_token"] == "Bearer test-caller-token"

    with jobs_lock:
        job = jobs[job_id]
    assert job["status"] == "completed"
    assert job["result"]["verified"] == 1
    assert job["result"]["details"][0]["matchedCrn"] == "26050700123452KKBK"

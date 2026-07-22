# Challan Verification Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a stateless `POST /tds/api/v1/challan/verify` endpoint that compares caller-supplied manual challans against government TRACES data (fetched via the existing `run_fetch()` pipeline) and reports Verified/Not Verified per challan, without touching any existing Import Challan code path.

**Architecture:** A new async job kind `"verify"` is added to the existing worker pool in `api_service.py`, reusing `run_fetch()` unmodified. A new pure module, `fetcher/services/challan_verification.py`, holds all normalization/matching logic and is unit-tested independently of FastAPI/Selenium. See [the design spec](../specs/2026-07-22-challan-verification-design.md) for full rationale.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest, httpx (for `fastapi.testclient.TestClient`).

---

### Task 1: Test tooling setup

**Files:**
- Create: `pytest.ini`
- Create: `requirements-dev.txt`

This repo currently has zero tests and no pytest config. Add the minimum needed to run tests that import top-level packages (`fetcher`, `api_service`) from the repo root.

- [ ] **Step 1: Create `pytest.ini`**

```ini
[pytest]
pythonpath = .
testpaths = tests
```

- [ ] **Step 2: Create `requirements-dev.txt`**

```
-r requirements.txt
pytest>=8.0.0
httpx>=0.27.0
```

- [ ] **Step 3: Create the `tests/` directory with a placeholder so it's tracked**

```bash
mkdir -p tests
```

- [ ] **Step 4: Verify pytest can collect (will report "no tests ran" — that's expected, it just confirms config is picked up)**

Run: `python3 -m pytest --collect-only`
Expected: `no tests ran` (exit code 5), no import/config errors.

- [ ] **Step 5: Commit**

```bash
git add pytest.ini requirements-dev.txt
git commit -m "Add pytest configuration and dev requirements"
```

---

### Task 2: Normalization helpers

**Files:**
- Create: `fetcher/services/challan_verification.py`
- Test: `tests/test_challan_verification.py`

Pure functions, no I/O. Reuses `fetcher.core.enrich._parse_date_str` (already handles `DD/MM/YYYY`, `YYYY-MM-DD`, `DD-Mon-YYYY`, and epoch-with-time variants) instead of duplicating date parsing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_challan_verification.py
from fetcher.services.challan_verification import (
    normalize_voucher,
    normalize_bsr,
    normalize_date,
    normalize_amount,
    build_key,
)


def test_normalize_voucher_strips_whitespace():
    assert normalize_voucher("  12345  ") == "12345"


def test_normalize_voucher_handles_none():
    assert normalize_voucher(None) == ""


def test_normalize_bsr_preserves_leading_zeros():
    assert normalize_bsr("0180002") == "0180002"
    assert normalize_bsr("0180002") != normalize_bsr("180002")


def test_normalize_date_handles_multiple_formats():
    assert normalize_date("07/05/2026") == "2026-05-07"
    assert normalize_date("2026-05-07") == "2026-05-07"
    assert normalize_date("07-May-2026 09:42:46") == "2026-05-07"


def test_normalize_date_returns_empty_string_when_unparseable():
    assert normalize_date("not-a-date") == ""


def test_normalize_amount_treats_equivalent_values_as_equal():
    assert normalize_amount(55140) == normalize_amount(55140.0) == normalize_amount("55140.00")


def test_normalize_amount_returns_empty_string_when_unparseable():
    assert normalize_amount("not-a-number") == ""


def test_build_key_combines_normalized_fields():
    key = build_key("0180002", "12345", "07/05/2026", 55140)
    assert key == "0180002|12345|2026-05-07|55140.00"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_challan_verification.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fetcher.services.challan_verification'`

- [ ] **Step 3: Write the implementation**

```python
# fetcher/services/challan_verification.py
"""
Pure in-memory matching logic for the challan verification endpoint.

No I/O, no FastAPI, no Selenium — every function here is a plain
transformation over dicts and is safe to unit test in isolation.
"""

from typing import Optional

from ..core.enrich import _parse_date_str


def normalize_voucher(value) -> str:
    """Trim whitespace. None becomes an empty string."""
    return str(value if value is not None else "").strip()


def normalize_bsr(value) -> str:
    """Trim whitespace only — never reformat digits, callers own leading zeros."""
    return str(value if value is not None else "").strip()


def normalize_date(value) -> str:
    """Parse many date formats and return 'YYYY-MM-DD', or '' if unparseable."""
    dt = _parse_date_str(str(value if value is not None else ""))
    return dt.strftime("%Y-%m-%d") if dt else ""


def normalize_amount(value) -> str:
    """Fixed 2-decimal string so 55140 / 55140.0 / '55140.00' all compare equal."""
    try:
        return f"{round(float(value), 2):.2f}"
    except (TypeError, ValueError):
        return ""


def build_key(bsr, voucher, date, amount) -> str:
    return "|".join((
        normalize_bsr(bsr),
        normalize_voucher(voucher),
        normalize_date(date),
        normalize_amount(amount),
    ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_challan_verification.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add fetcher/services/challan_verification.py tests/test_challan_verification.py
git commit -m "Add normalization helpers for challan verification matching"
```

---

### Task 3: Date range computation

**Files:**
- Modify: `fetcher/services/challan_verification.py`
- Modify: `tests/test_challan_verification.py`

`compute_date_range` derives `FROM_DATE`/`TO_DATE` (for the `run_fetch()` cfg override) from the min/max `dateOfDeposit` across all manual challans in the request, per the spec's "Determine Date Range Automatically" step. Raises `ValueError` on any unparseable date so the endpoint can fail fast with a 422 before touching TRACES.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_challan_verification.py`:

```python
import pytest

from fetcher.services.challan_verification import compute_date_range


def test_compute_date_range_returns_min_and_max():
    manual = [
        {"id": "a", "dateOfDeposit": "07/05/2026"},
        {"id": "b", "dateOfDeposit": "05/06/2026"},
        {"id": "c", "dateOfDeposit": "01/07/2026"},
    ]
    assert compute_date_range(manual) == ("07/05/2026", "01/07/2026")


def test_compute_date_range_single_challan():
    manual = [{"id": "a", "dateOfDeposit": "11/04/2026"}]
    assert compute_date_range(manual) == ("11/04/2026", "11/04/2026")


def test_compute_date_range_raises_on_unparseable_date():
    manual = [{"id": "a", "dateOfDeposit": "not-a-date"}]
    with pytest.raises(ValueError, match="Unparseable dateOfDeposit"):
        compute_date_range(manual)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_challan_verification.py -v -k compute_date_range`
Expected: FAIL — `ImportError: cannot import name 'compute_date_range'`

- [ ] **Step 3: Add the implementation**

Append to `fetcher/services/challan_verification.py`:

```python
def compute_date_range(manual_challans: list[dict]) -> tuple[str, str]:
    """
    Return (from_date, to_date) as DD/MM/YYYY strings spanning every manual
    challan's dateOfDeposit. Raises ValueError if any date is unparseable.
    """
    dates = []
    for item in manual_challans:
        raw = item.get("dateOfDeposit")
        dt = _parse_date_str(str(raw or ""))
        if dt is None:
            raise ValueError(
                f"Unparseable dateOfDeposit for challan id={item.get('id')!r}: {raw!r}"
            )
        dates.append(dt)
    return min(dates).strftime("%d/%m/%Y"), max(dates).strftime("%d/%m/%Y")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_challan_verification.py -v -k compute_date_range`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add fetcher/services/challan_verification.py tests/test_challan_verification.py
git commit -m "Add automatic date range computation for challan verification"
```

---

### Task 4: The matching function

**Files:**
- Modify: `fetcher/services/challan_verification.py`
- Modify: `tests/test_challan_verification.py`

This is the O(n) comparison: build one dict from the government challans, then look up each manual challan by its own composite key. Covers every matching scenario from the spec.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_challan_verification.py`:

```python
from fetcher.services.challan_verification import verify_challans


def _gov(bsr, voucher, date, amount, crn):
    return {"bsrCode": bsr, "challanNum": voucher, "tenderDt": date, "totalAmt": amount, "crn": crn}


def _manual(id_, bsr, voucher, date, amount):
    return {"id": id_, "bsrCode": bsr, "voucherNo": voucher, "dateOfDeposit": date, "totalAmount": amount}


def test_single_verified_challan():
    manual = [_manual("m1", "0180002", "37358", "07/05/2026", 52830)]
    government = [_gov("0180002", "37358", "07/05/2026", 52830, "CRN1")]
    result = verify_challans(manual, government)
    assert result["totalManual"] == 1
    assert result["verified"] == 1
    assert result["notVerified"] == 0
    assert result["details"][0]["status"] == "Verified"
    assert result["details"][0]["matchedCrn"] == "CRN1"
    assert result["message"] == "Verification completed."


def test_multiple_verified_challans():
    manual = [
        _manual("m1", "0180002", "37358", "07/05/2026", 52830),
        _manual("m2", "0180002", "17758", "05/06/2026", 45000),
    ]
    government = [
        _gov("0180002", "37358", "07/05/2026", 52830, "CRN1"),
        _gov("0180002", "17758", "05/06/2026", 45000, "CRN2"),
    ]
    result = verify_challans(manual, government)
    assert result["verified"] == 2
    assert result["notVerified"] == 0


def test_no_matches():
    manual = [_manual("m1", "0180002", "37358", "07/05/2026", 52830)]
    government = [_gov("0180002", "99999", "07/05/2026", 52830, "CRNX")]
    result = verify_challans(manual, government)
    assert result["verified"] == 0
    assert result["notVerified"] == 1
    assert result["details"][0]["status"] == "Not Verified"
    assert result["details"][0]["matchedCrn"] is None
    assert result["message"] == "Verification completed. 0 challans verified."


def test_duplicate_voucher_numbers_different_bsr_codes():
    manual = [
        _manual("m1", "0180002", "12345", "07/05/2026", 1000),
        _manual("m2", "0987654", "12345", "07/05/2026", 2000),
    ]
    government = [
        _gov("0180002", "12345", "07/05/2026", 1000, "CRN1"),
        _gov("0987654", "12345", "07/05/2026", 2000, "CRN2"),
    ]
    result = verify_challans(manual, government)
    assert result["verified"] == 2
    by_id = {d["id"]: d for d in result["details"]}
    assert by_id["m1"]["matchedCrn"] == "CRN1"
    assert by_id["m2"]["matchedCrn"] == "CRN2"


def test_amount_normalization_in_matching():
    manual = [_manual("m1", "0180002", "37358", "07/05/2026", 55140.0)]
    government = [_gov("0180002", "37358", "07/05/2026", "55140.00", "CRN1")]
    result = verify_challans(manual, government)
    assert result["details"][0]["status"] == "Verified"


def test_empty_government_list_marks_all_not_verified():
    manual = [_manual("m1", "0180002", "37358", "07/05/2026", 52830)]
    result = verify_challans(manual, [])
    assert result["verified"] == 0
    assert result["notVerified"] == 1
    assert result["governmentFetched"] == 0
    assert result["message"] == "No challans found on the government portal for the selected date range."


def test_no_manual_challans_returns_zero_totals():
    result = verify_challans([], [_gov("0180002", "37358", "07/05/2026", 52830, "CRN1")])
    assert result["totalManual"] == 0
    assert result["verified"] == 0
    assert result["notVerified"] == 0
    assert result["details"] == []


def test_from_date_to_date_passthrough():
    result = verify_challans([], [], from_date="07/05/2026", to_date="01/07/2026")
    assert result["fromDate"] == "07/05/2026"
    assert result["toDate"] == "01/07/2026"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_challan_verification.py -v -k verify_challans`
Expected: FAIL — `ImportError: cannot import name 'verify_challans'`

- [ ] **Step 3: Add the implementation**

Append to `fetcher/services/challan_verification.py`:

```python
def verify_challans(
    manual: list[dict],
    government: list[dict],
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:
    """
    Compare manual challans against government-fetched challans using an
    O(n) composite-key lookup, and return the verification result payload.

    Government field mapping (from the existing, unmodified enrich() output):
    bsrCode, challanNum (voucher/serial), tenderDt (falls back conceptually
    to paymentDt upstream), totalAmt.
    """
    gov_index: dict = {}
    for g in government:
        key = build_key(
            g.get("bsrCode"), g.get("challanNum"),
            g.get("tenderDt") or g.get("paymentDt"), g.get("totalAmt"),
        )
        gov_index[key] = g.get("crn")

    details = []
    verified = 0
    for item in manual:
        key = build_key(
            item.get("bsrCode"), item.get("voucherNo"),
            item.get("dateOfDeposit"), item.get("totalAmount"),
        )
        matched_crn = gov_index.get(key)
        status = "Verified" if matched_crn is not None else "Not Verified"
        if status == "Verified":
            verified += 1
        details.append({
            "id":         item.get("id"),
            "voucherNo":  item.get("voucherNo"),
            "status":     status,
            "matchedCrn": matched_crn,
        })

    total = len(manual)
    not_verified = total - verified

    if not government:
        message = "No challans found on the government portal for the selected date range."
    elif verified == 0:
        message = "Verification completed. 0 challans verified."
    else:
        message = "Verification completed."

    return {
        "success":           True,
        "message":           message,
        "totalManual":       total,
        "verified":          verified,
        "notVerified":       not_verified,
        "fromDate":          from_date,
        "toDate":            to_date,
        "governmentFetched": len(government),
        "details":           details,
    }
```

- [ ] **Step 4: Run all tests in the file to verify they pass**

Run: `python3 -m pytest tests/test_challan_verification.py -v`
Expected: 20 passed (8 from Task 2 + 3 from Task 3 + 9 from this task)

- [ ] **Step 5: Commit**

```bash
git add fetcher/services/challan_verification.py tests/test_challan_verification.py
git commit -m "Add verify_challans matching function with full spec test coverage"
```

---

### Task 5: Wire the verify job into api_service.py

**Files:**
- Modify: `api_service.py`
- Test: `tests/test_api_service_verify.py`

This task adds the new job kind, endpoint, and request models — reusing `run_fetch()` unmodified and touching the existing `/fetch` code path in exactly one place (the TAN-dedup check).

- [ ] **Step 1: Add the import**

Modify `api_service.py` — after line 17 (`from fetcher.utils.config import CONFIG as _CFG`):

```python
from fetcher.services import challan_verification
```

- [ ] **Step 2: Add the request models**

Modify `api_service.py` — after the `FetchRequest` class (currently lines 367-372), before `class EntityRequest`:

```python
class ManualChallanItem(BaseModel):
    id:            str
    voucherNo:     str
    bsrCode:       str
    dateOfDeposit: str
    totalAmount:   float


class VerifyChallansRequest(BaseModel):
    tan:            str
    password:       str
    financialYear:  Optional[str] = None
    manual_challans: list[ManualChallanItem] = []
```

- [ ] **Step 3: Widen `_active_tan_job` to check multiple kinds**

Modify `api_service.py` — replace the existing function (currently lines 335-344):

```python
def _active_tan_job(tan: str, kinds="fetch"):
    """Return the most recent active (pending/running) job for this TAN
    whose type is in `kinds` (a single kind string, or an iterable of them),
    or None."""
    kind_set = (kinds,) if isinstance(kinds, str) else tuple(kinds)
    with jobs_lock:
        active = [
            j for j in jobs.values()
            if j.get("tan") == tan
            and j.get("type", "fetch") in kind_set
            and j.get("status") in ("pending", "running")
        ]
    return active[-1] if active else None
```

This is backward compatible: existing callers passing a single string (`"fetch"`, `"entity"`, `"view_filed_forms"`) behave identically to before.

- [ ] **Step 4: Update the `/fetch` endpoint's dedup call to also consider `verify` jobs**

Modify `api_service.py` — inside `create_fetch_job` (currently line 535):

```python
    existing = _active_tan_job(req.tan, ("fetch", "verify"))
```

(This is the one line changed in the existing `/fetch` path. It only changes behavior when a `verify` job is already active for that TAN — a case that's impossible today since `verify` doesn't exist yet — so it does not alter current `/fetch` behavior at all.)

- [ ] **Step 5: Add the `verify` job runner**

Modify `api_service.py` — insert after `_run_view_filed_forms_job` (currently ends at line 527), before the `# ── Endpoints ──` comment:

```python
def _run_verify_job(job_id: str, req: VerifyChallansRequest) -> None:
    import time
    t0 = time.time()
    logger.info(
        "Verify job %s started for TAN %s — %d manual challans",
        job_id, req.tan, len(req.manual_challans),
    )
    _patch_job(job_id, status="running", started_at=datetime.now().isoformat())

    manual = [item.model_dump() for item in req.manual_challans]

    try:
        from_date, to_date = challan_verification.compute_date_range(manual)
        logger.info("Verify job %s: computed date range %s → %s", job_id, from_date, to_date)

        job_dir     = os.path.join(DOWNLOADS_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        output_file = os.path.join(job_dir, f"TDS_Challans_{req.tan}.xlsx")

        cfg = {
            "TAN":            req.tan,
            "PASSWORD":       req.password,
            "FROM_DATE":      from_date,
            "TO_DATE":        to_date,
            "FINANCIAL_YEAR": req.financialYear,
            "OUTPUT_PATH":    output_file,
            "DOWNLOAD_DIR":   job_dir,
        }

        path, _count, _grand = run_fetch(cfg)
        json_path = path.rsplit(".", 1)[0] + ".json"
        government: list = []
        if os.path.exists(json_path):
            with open(json_path, encoding="utf-8") as f:
                government = json.load(f)

        result = challan_verification.verify_challans(manual, government, from_date, to_date)

        _patch_job(
            job_id,
            status="completed",
            completed_at=datetime.now().isoformat(),
            result=result,
        )
        logger.info(
            "Verify job %s done: %d manual, %d verified, %d not verified "
            "(%d government challans fetched) in %.1fs",
            job_id, result["totalManual"], result["verified"], result["notVerified"],
            result["governmentFetched"], time.time() - t0,
        )
    except Exception:
        logger.exception("Verify job %s failed after %.1fs", job_id, time.time() - t0)
        _patch_job(
            job_id,
            status="failed",
            completed_at=datetime.now().isoformat(),
            error="Unable to fetch challans from Income Tax portal.",
        )
```

- [ ] **Step 6: Dispatch the new job kind in the worker**

Modify `api_service.py` — inside `_worker()` (currently lines 309-330), add a branch after the `view_filed_forms` branch:

```python
            elif kind == "view_filed_forms":
                _run_view_filed_forms_job(job_id, req)
            elif kind == "verify":
                _run_verify_job(job_id, req)
            else:
```

- [ ] **Step 7: Add the endpoint**

Modify `api_service.py` — insert immediately after `create_fetch_job` (currently ends at line 565), before `get_job`:

```python
@app.post("/tds/api/v1/challan/verify")
async def create_verify_job(req: VerifyChallansRequest):
    if not req.manual_challans:
        return {
            "success":     True,
            "message":     "No manual challans found.",
            "totalManual": 0,
            "verified":    0,
            "notVerified": 0,
            "details":     [],
        }

    manual = [item.model_dump() for item in req.manual_challans]
    try:
        challan_verification.compute_date_range(manual)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    existing = _active_tan_job(req.tan, ("fetch", "verify"))
    if existing:
        return {
            "job_id":  existing["id"],
            "status":  existing["status"],
            "message": (
                f"A {existing.get('type', 'fetch')} job for TAN {req.tan} is already "
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
            "type":       "verify",
            "status":     "pending",
            "created_at": datetime.now().isoformat(),
        }
    _save_jobs()
    _job_queue.put((job_id, "verify", req))
    pos = _queue_position(job_id)
    return {"job_id": job_id, "status": "pending", "queue_position": pos}
```

- [ ] **Step 8: Write the integration tests**

```python
# tests/test_api_service_verify.py
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
```

- [ ] **Step 9: Run the integration tests**

Run: `python3 -m pytest tests/test_api_service_verify.py -v`
Expected: 6 passed

If this fails with `ModuleNotFoundError` for a dependency (e.g. `pydantic_settings`, `ddddocr`), install the full project dependencies first: `pip install -r requirements-dev.txt` (use a virtualenv if the system Python is externally managed: `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt`).

- [ ] **Step 10: Run the full test suite to confirm nothing else broke**

Run: `python3 -m pytest -v`
Expected: 26 passed (20 from `test_challan_verification.py` + 6 from `test_api_service_verify.py`)

- [ ] **Step 11: Commit**

```bash
git add api_service.py tests/test_api_service_verify.py
git commit -m "Add POST /tds/api/v1/challan/verify endpoint"
```

---

### Task 6: Documentation and final check

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the new endpoint to the README's TDS Endpoints list**

Modify `README.md` — in the `### TDS Endpoints` section, after the line `- `POST /tds/api/v1/entity`: Retrieves the Deductor entity profile.`:

```markdown
- `POST /tds/api/v1/challan/verify`: Queues a background job that compares caller-supplied manual challans against TRACES data for the date range spanning them, and returns a Verified/Not Verified result per challan via the same job-polling mechanism as `/fetch`.
```

- [ ] **Step 2: Run the full test suite one final time**

Run: `python3 -m pytest -v`
Expected: 26 passed

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document the challan verification endpoint in README"
```

---

## Manual verification note

This plan's automated tests mock `run_fetch()` and never contact the real TRACES portal — there's no way to exercise a real Selenium login inside this environment (no valid TAN/password, no live browser target). Before relying on this in production, do one manual smoke test against a real TAN using `curl`:

```bash
curl -X POST http://localhost:8001/tds/api/v1/challan/verify \
  -H "Content-Type: application/json" \
  -d '{"tan":"<REAL_TAN>","password":"<REAL_PASSWORD>","manual_challans":[{"id":"1","voucherNo":"...","bsrCode":"...","dateOfDeposit":"DD/MM/YYYY","totalAmount":0}]}'
```

Then poll `GET /tds/api/v1/jobs/{job_id}` (already-existing, unmodified endpoint) until `status` is `completed` or `failed`.

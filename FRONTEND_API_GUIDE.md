# TRACES Portal Backend — Frontend Integration Guide

All APIs served by one FastAPI app (`api_service.py`).

## Base URL

```
http://<host>:8001
```

(Container exposes and maps port `8001:8001`. Replace `<host>` with the deployed server address.)

## ⚠️ Known gaps — read before integrating

1. **No CORS middleware is configured.** Direct browser calls from a different origin (e.g. `http://localhost:3000` → `http://server:8001`) will currently be blocked by the browser. This needs to be fixed backend-side (or routed through a same-origin proxy) before the frontend can call these APIs directly from JS running in the browser. Flag this to the backend team if you hit CORS errors.
2. **TBR, TDS/TCS, Justification, and CONSO modules are backend skeletons.** `initiate` endpoints create a tracked request (status `INITIATED`) and persist it, but none of them call the real TRACES backend yet or produce a real file. Status will not progress past `INITIATED`, and **there is no download endpoint for these four modules yet** (unlike PAN Verification and TDS Challan, which do have real file downloads). Build UI and polling against these contracts now — the response shapes are final — but downloads for these 4 modules aren't wired up yet.
3. **PAN Verification and TDS Challan Fetch are fully functional today**, including real TRACES login, captcha, and file downloads.

## Error format

All errors follow FastAPI's standard shape:

```json
{ "detail": "Human readable error message" }
```

Common status codes: `400` (validation), `404` (not found), `500` (server error), `503` (service unavailable — e.g. PAN Verification failed to start).

---

## 1. TDS Challan Fetch — `/tds/api/v1/*`

Fully functional. Async job pattern: create a job → poll or use WebSocket → download result.

### `POST /tds/api/v1/fetch`
Start a TDS challan fetch job.

**Request body**
```json
{
  "tan": "PTLA13241E",
  "password": "string",
  "from_date": "01-Apr-2026",
  "to_date": "30-Jun-2026",
  "financialYear": "2026-27"
}
```
`financialYear` is optional.

**Response `200`**
```json
{ "job_id": "uuid", "status": "pending", "queue_position": 1 }
```
If a job for this TAN is already active, returns the existing job instead of creating a new one (same shape, plus `"message"`).
`503` if queue is full (`>= 10` pending jobs).

### `GET /tds/api/v1/jobs/{job_id}`
Poll job status.

**Response**
```json
{
  "id": "uuid",
  "tan": "PTLA13241E",
  "type": "fetch",
  "status": "pending | running | completed | failed",
  "created_at": "ISO8601",
  "started_at": "ISO8601 (once running)",
  "completed_at": "ISO8601 (once done)",
  "queue_position": 1,
  "result": {
    "excel_file": "path",
    "json_file": "path or null",
    "record_count": 42,
    "grand_total": 123456.0,
    "entity_profile_file": "path or null"
  }
}
```
`result` only present once `status == completed`. `404` if job doesn't exist.

### `WS /tds/api/v1/jobs/{job_id}/ws`
WebSocket alternative to polling — pushes a status message every 2s until the job reaches `completed`/`failed`, then closes.

Messages:
```json
{ "status": "pending", "queue_position": 1 }
{ "status": "running" }
{ "status": "completed", "result": { "...same as above..." } }
{ "status": "failed", "error": "Job failed during execution." }
{ "status": "not_found", "message": "Job not found" }
```

### `GET /tds/api/v1/jobs/{job_id}/download`
Downloads the generated Excel file (binary attachment). Only valid once `status == completed`. `400` if not completed, `404` if file missing.

### `GET /tds/api/v1/jobs/{job_id}/data`
Returns the same challan data as enriched JSON (array) instead of the Excel file. Only valid once completed.

### `GET /tds/api/v1/jobs`
Returns array of **all** jobs (all types, all statuses) — useful for an admin/history view.

### `GET /tds/api/v1/queue`
Real-time queue snapshot.
```json
{
  "workers": 2,
  "running": 1,
  "pending": 3,
  "capacity_remaining": 7,
  "running_jobs": [{ "job_id": "...", "tan": "...", "started_at": "..." }],
  "queued_jobs": [{ "job_id": "...", "tan": "...", "queue_position": 1, "waiting_since": "..." }]
}
```

### `POST /tds/api/v1/entity`
Fetch (and cache) the deductor entity profile for a TAN.

**Request**
```json
{ "tan": "PTLA13241E", "password": "string" }
```
**Response**: same job shape as `/fetch`. If a cached profile exists and is **< 7 days old**, returns immediately with `status: "completed"` (no actual fetch performed) — cheap to call speculatively.

### `GET /tds/api/v1/entity/{tan}`
Returns the cached entity profile JSON directly (raw TRACES structure, not fixed schema). `404` if never fetched — call `POST /entity` or `POST /fetch` first.

### `POST /tds/api/v1/view-filed-forms`
**Request**
```json
{ "tan": "PTLA13241E", "password": "string", "form_type_cd": "F27EQ" }
```
**Response**: same job shape as `/fetch`.

### `GET /tds/api/v1/view-filed-forms/{tan}/{form_type_cd}`
Returns cached filed-forms array once the job above completes:
```json
[
  {
    "tempAckNo": "...", "ackDt": "...", "ackNum": "...",
    "filingTypeCd": "...", "fillingMode": "...", "activities": "...",
    "financialQrtr": "...", "formTypeCd": "F27EQ", "refYear": "..."
  }
]
```
`404` if not yet fetched.

### `GET /health` and `GET /tds/api/health`
```json
// GET /health
{ "status": "healthy | degraded", "services": { "tds": "healthy", "pan_verification": "healthy | unavailable" } }

// GET /tds/api/health
{ "status": "ok", "timestamp": "ISO8601", "workers": 2, "running": 0, "pending": 0 }
```

---

## 2. PAN Verification — `/pan-verification/*`

Fully functional. Two-step login (manual captcha) or fully-automated (auto-solved captcha) flows.

> All routes below return `503` with `{"detail": "PAN Verification service is currently unavailable."}` if the browser automation backend failed to start.

### Manual login flow

**`POST /pan-verification/login/init`**
```json
// Request
{ "tan": "PTLA13241E", "password": "string" }
// Response
{
  "session_id": "uuid",
  "captcha_base64": "iVBORw0KG...",
  "captcha_id": "string",
  "message": "Decode captcha_base64 image and provide the text in /login/complete"
}
```
Decode `captcha_base64` and display to the user for manual entry.

**`POST /pan-verification/login/complete`**
```json
// Request
{ "session_id": "uuid", "tan": "PTLA13241E", "password": "string", "captcha": "5g7kGb" }
// Response
{ "success": true, "session_id": "uuid", "logged_in": true }
```
If captcha is wrong, call `/login/init` again for a fresh captcha (or use resend below).

**`POST /pan-verification/captcha/resend`**
```json
// Request
{ "session_id": "uuid" }
// Response: same shape as /login/init
```

### Single PAN verify

**`POST /pan-verification/pan/verify`**
```json
// Request
{ "session_id": "uuid", "pan": "AAAAA9999A", "form_type": "24Q" }
// Response
{ "pan": "AAAAA9999A", "holder_name": "string|null", "status": "string|null", "is_valid": true }
```

### Bulk PAN verify (manual session)

**`POST /pan-verification/pan/bulk-upload`** — `multipart/form-data`
Fields: `session_id` (string), `file` (CSV file)
```json
// Response
{ "success": true, "token_number": "string", "message": "string|null" }
```

**`GET /pan-verification/pan/status/{token_number}?session_id=...`**
```json
{ "token_number": "string", "status": "Submitted|Available|...", "is_ready": false }
```

**`GET /pan-verification/pan/download/{token_number}?session_id=...`**
Binary file download. Poll status until `is_ready: true` first.

### Fully automated bulk PAN (no manual captcha — server solves it)

Same contracts as above, but pass `tan` + `password` instead of `session_id` — server auto-logs-in (or reuses an existing session) transparently:

- **`POST /pan-verification/pan/auto/bulk-upload`** — multipart: `tan`, `password`, `file`
- **`GET /pan-verification/pan/auto/status/{token_number}?tan=...&password=...`**
- **`GET /pan-verification/pan/auto/download/{token_number}?tan=...&password=...`**

### Session & health

**`GET /pan-verification/session/status/{session_id}`**
```json
{ "active": true, "expired": false }
```

**`GET /pan-verification/health`**
```json
{ "status": "healthy", "message": "TRACES PAN Verification Service is running" }
```

---

## 3. Transaction Based Report (TBR) — `/api/v1/tbr/*`

### `POST /api/v1/tbr/validate-tan`
```json
// Request
{ "tan": "PTLA13241E", "finYear": "2026", "quarter": "3" }
// Response
{
  "isValid": true,
  "message": "Statement available",
  "tan": "PTLA13241E",
  "finYear": "2026",
  "quarter": 3,
  "processingStatus": ""
}
```
> ⚠️ Currently always returns `isValid: true` (mock) — real availability check not yet implemented.

### `GET /api/v1/tbr/ready-download-requests?tanId=...&page=0&size=10`
```json
{
  "requests": [
    {
      "id": 1,
      "tan": "PTLA13241E",
      "financial_year": 2026,
      "quarter": "3",
      "request_id": "TBR_PTLA13241E_2026_3_a1b2c3d4",
      "status": "INITIATED",
      "processing_status": "Queued for processing",
      "total_records": 0,
      "processed_records": 0,
      "initiated_date": "ISO8601",
      "completed_date": null
    }
  ],
  "currentPage": 0,
  "pageSize": 10,
  "totalItems": 1,
  "totalPages": 1,
  "httpStatus": 200
}
```
> ⚠️ Filters on `status IN (COMPLETED, READY)` — since nothing transitions past `INITIATED` yet, this will always return empty until backend processing is implemented.

### `POST /api/v1/tbr/initiate?tan=...&finYear=...&quarter=...`
**Query params, not a JSON body.** Returns the created `TransactionBasedReportDTO` (same shape as one item in the list above).

### `GET /api/v1/tbr/status/{request_id}`
```json
{
  "request_id": "TBR_PTLA13241E_2026_3_a1b2c3d4",
  "status": "INITIATED",
  "processing_status": "Queued for processing",
  "total_records": 0,
  "processed_records": 0,
  "initiated_date": "ISO8601",
  "completed_date": null,
  "error_message": null
}
```
`404` if request_id doesn't exist.

---

## 4. TDS/TCS Certificate Download — `/api/v1/tdstcs/*`

### `GET /api/v1/tdstcs/form-types`
```json
{ "form_types": [{ "code": "131", "description": "Form No. 131" }, { "code": "133", "description": "Form No. 133" }], "message": "..." }
```
> Static/mocked — matches TRACES' actual accepted values (`131`, `133`).

### `GET /api/v1/tdstcs/quarters?financial_year=2026-27`
```json
{ "quarters": [
  { "code": "3", "description": "Q1", "displayName": "Q1" },
  { "code": "4", "description": "Q2", "displayName": "Q2" },
  { "code": "1", "description": "Q3", "displayName": "Q3" },
  { "code": "2", "description": "Q4", "displayName": "Q4" }
], "message": "..." }
```

### `POST /api/v1/tdstcs/initiate`
```json
// Request
{ "tan": "PTLA13241E", "financial_year": "2026-27", "quarter": "Q1", "form_type": "131" }
// Response
{ "request_id": "TDSTCS_PTLA13241E_2026_27_Q1_a1b2c3d4", "status": "INITIATED", "message": "Certificate download initiated", "transaction_id": null }
```

### `GET /api/v1/tdstcs/status/{request_id}`
```json
{ "request_id": "...", "status": "INITIATED", "is_ready": false, "message": "Queued for download", "progress": 50, "file_path": null }
```
`progress` is `100` once `status == COMPLETED`, else `50` — coarse placeholder, not a real percentage yet.

### `GET /api/v1/tdstcs/list?tan=...&page=0&page_size=10`
```json
{ "certificates": [], "total": 0, "page": 0, "message": "..." }
```
Only returns requests with `status == COMPLETED` — will be empty until backend processing exists.

---

## 5. Justification Report — `/api/v1/justification/*`

### `GET /api/v1/justification/financial-years`
```json
{ "financial_years": [{ "code": "2026", "description": "2026-27", "yearValue": 2026 }], "message": "..." }
```

### `GET /api/v1/justification/form-types?tan=...&financial_year=2026`
```json
{ "form_types": [], "message": "..." }
```
> ⚠️ Currently always returns an empty list (mirrors TRACES returning 404 "Data not found" when no justification data exists for the TAN). Not yet wired to a real lookup.

### `POST /api/v1/justification/initiate`
```json
// Request
{ "tan": "PTLA13241E", "financial_year": "2026", "form_type": "FORM1" }
// Response
{ "request_id": "JUST_PTLA13241E_2026_FORM1_a1b2c3d4", "status": "INITIATED", "message": "Justification report initiated", "transaction_id": null }
```

### `GET /api/v1/justification/status/{request_id}`
```json
{ "request_id": "...", "status": "INITIATED", "is_ready": false, "message": "INITIATED", "progress": 50 }
```

### `GET /api/v1/justification/list?tan=...&page=0&page_size=10`
```json
{ "reports": [], "total": 0, "page": 0, "message": "..." }
```

---

## 6. CONSO File Download — `/api/v1/conso/*`

### `GET /api/v1/conso/quarters?financial_year=2026`
```json
{ "quarters": [{ "code": "3", "description": "Q1", "displayName": "Q1" }], "message": "..." }
```
> Static/mocked — currently always returns Q1 only regardless of year.

### `POST /api/v1/conso/initiate`
```json
// Request
{ "tan": "PTLA13241E", "financial_year": "2026", "quarter": "Q1", "form_type": "143" }
// Response
{ "request_id": "CONSO_PTLA13241E_2026_Q1_a1b2c3d4", "status": "INITIATED", "message": "CONSO download initiated", "request_ids": null }
```

### `GET /api/v1/conso/status/{request_id}`
```json
{ "request_id": "...", "status": "INITIATED", "is_ready": false, "message": "INITIATED", "progress": 50 }
```

### `GET /api/v1/conso/list?tan=...&page=0&page_size=10`
```json
{
  "requests": [
    {
      "request_id": "CONSO_PTLA13241E_2026_Q1_a1b2c3d4",
      "tan": "PTLA13241E",
      "financial_year": "2026",
      "quarter": "Q1",
      "form_type": "143",
      "status": "INITIATED",
      "initiated_date": "ISO8601",
      "completed_date": null
    }
  ],
  "current_page": 0,
  "total_pages": 1,
  "total_elements": 1,
  "page_size": 10,
  "has_next": false,
  "has_previous": false,
  "message": "..."
}
```
Unlike the other 3 new modules, this list is **not** filtered by status — returns all CONSO requests for the TAN regardless of state.

---

## Quick reference — all endpoints

| Module | Method | Path | Body/Query |
|---|---|---|---|
| TDS Challan | POST | `/tds/api/v1/fetch` | body |
| TDS Challan | GET | `/tds/api/v1/jobs/{job_id}` | — |
| TDS Challan | WS | `/tds/api/v1/jobs/{job_id}/ws` | — |
| TDS Challan | GET | `/tds/api/v1/jobs/{job_id}/download` | — |
| TDS Challan | GET | `/tds/api/v1/jobs/{job_id}/data` | — |
| TDS Challan | GET | `/tds/api/v1/jobs` | — |
| TDS Challan | GET | `/tds/api/v1/queue` | — |
| TDS Challan | POST | `/tds/api/v1/entity` | body |
| TDS Challan | GET | `/tds/api/v1/entity/{tan}` | — |
| TDS Challan | POST | `/tds/api/v1/view-filed-forms` | body |
| TDS Challan | GET | `/tds/api/v1/view-filed-forms/{tan}/{form_type_cd}` | — |
| PAN | POST | `/pan-verification/login/init` | body |
| PAN | POST | `/pan-verification/login/complete` | body |
| PAN | POST | `/pan-verification/captcha/resend` | body |
| PAN | POST | `/pan-verification/pan/verify` | body |
| PAN | POST | `/pan-verification/pan/bulk-upload` | multipart |
| PAN | GET | `/pan-verification/pan/status/{token_number}` | query: `session_id` |
| PAN | GET | `/pan-verification/pan/download/{token_number}` | query: `session_id` |
| PAN | POST | `/pan-verification/pan/auto/bulk-upload` | multipart |
| PAN | GET | `/pan-verification/pan/auto/status/{token_number}` | query: `tan`, `password` |
| PAN | GET | `/pan-verification/pan/auto/download/{token_number}` | query: `tan`, `password` |
| PAN | GET | `/pan-verification/session/status/{session_id}` | — |
| TBR | POST | `/api/v1/tbr/validate-tan` | body |
| TBR | GET | `/api/v1/tbr/ready-download-requests` | query: `tanId`, `page`, `size` |
| TBR | POST | `/api/v1/tbr/initiate` | query: `tan`, `finYear`, `quarter` |
| TBR | GET | `/api/v1/tbr/status/{request_id}` | — |
| TDS/TCS | GET | `/api/v1/tdstcs/form-types` | — |
| TDS/TCS | GET | `/api/v1/tdstcs/quarters` | query: `financial_year` |
| TDS/TCS | POST | `/api/v1/tdstcs/initiate` | body |
| TDS/TCS | GET | `/api/v1/tdstcs/status/{request_id}` | — |
| TDS/TCS | GET | `/api/v1/tdstcs/list` | query: `tan`, `page`, `page_size` |
| Justification | GET | `/api/v1/justification/financial-years` | — |
| Justification | GET | `/api/v1/justification/form-types` | query: `tan`, `financial_year` |
| Justification | POST | `/api/v1/justification/initiate` | body |
| Justification | GET | `/api/v1/justification/status/{request_id}` | — |
| Justification | GET | `/api/v1/justification/list` | query: `tan`, `page`, `page_size` |
| CONSO | GET | `/api/v1/conso/quarters` | query: `financial_year` |
| CONSO | POST | `/api/v1/conso/initiate` | body |
| CONSO | GET | `/api/v1/conso/status/{request_id}` | — |
| CONSO | GET | `/api/v1/conso/list` | query: `tan`, `page`, `page_size` |
| Monitoring | GET | `/health` | — |
| Monitoring | GET | `/tds/api/health` | — |
| Monitoring | GET | `/pan-verification/health` | — |

## Interactive docs

Full auto-generated schema browser (try requests live) at:
```
http://<host>:8001/docs
```

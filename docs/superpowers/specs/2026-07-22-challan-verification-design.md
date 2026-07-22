# Challan Verification API — Design

## Context

The main TaxVahan backend (a separate repository, not available in this workspace) lets users
manually enter TDS challans. Government-imported challans always carry a `SectionCode`;
manually entered ones don't, until verified. The main backend wants a "Verify Challans" feature
that checks manually entered challans against the government TRACES portal and marks them
Verified/Not Verified.

This repository (`income-tax-app`) is the stateless FastAPI automation microservice that already
drives Selenium against TRACES for the existing "Import Challan" feature
(`POST /tds/api/v1/fetch` → async job → `GET /tds/api/v1/jobs/{job_id}`). It has no database and
owns no `Company`/`Deductor`/`Challan` records — those live entirely in the other backend.

**Decision (confirmed with the user):** the DB-owning parts of challan verification (reading
manual challans by Company/FY/Quarter/Deductor, persisting `Verified`/`VerifiedAt`) are out of
scope here and remain the other backend's responsibility. This repo adds a stateless verification
endpoint: the caller sends the manual challans to check in the request body; this service fetches
government data for the computed date range and returns Verified/Not Verified per challan. It
writes nothing to any database.

## Non-goals

- No DB models, no MySQL connection, no persistence of verification results in this repo.
- No changes to the existing `/tds/api/v1/fetch` endpoint, `_run_job`, `run_fetch`, or anything
  under `fetcher/` that the existing Import Challan flow depends on.
- No frontend work (the "Verify Challans" button lives in the other repo's UI).

## Architecture

A new job kind, `"verify"`, is added next to the existing `"fetch"`, `"entity"`, and
`"view_filed_forms"` kinds in `api_service.py`'s worker pool. It reuses `run_fetch()` — the exact
function the existing `/fetch` job already calls — so the TRACES fetch logic itself is not
duplicated or modified.

```
Other backend                          income-tax-app (this repo)
──────────────                         ───────────────────────────
POST /tds/api/v1/challan/verify   ───▶  validate body
  { tan, password, financialYear,       - manual_challans empty →
    manual_challans: [                    return 200 immediately, no job
      { id, voucherNo, bsrCode,         - else: compute fromDate/toDate
        dateOfDeposit, totalAmount },     = min/max(dateOfDeposit)
      ...                                 enqueue job (kind="verify")
    ] }                                   return { job_id, status: pending }

GET /tds/api/v1/jobs/{job_id}    ───▶  (EXISTING endpoint, unchanged)
  (poll, or existing                    returns job incl. result once
   /jobs/{id}/ws websocket)              worker finishes

Worker thread (_run_verify_job, new):
  cfg = { TAN, PASSWORD, FROM_DATE, TO_DATE, FINANCIAL_YEAR, ... }
  path, count, grand = run_fetch(cfg)       # same call _run_job already makes
  government = json.load(<path>.json)       # same sidecar file /fetch already writes
  result = verify_challans(manual, government)   # new pure module
  job["result"] = result
```

### TAN concurrency (small shared-code touch)

`_active_tan_job()` currently dedupes only within the same job kind. Since `verify` also drives a
live Selenium session against the same TRACES account, extend the check so an in-flight `fetch`
job for a TAN blocks a new `verify` job for that TAN, and vice versa — otherwise two concurrent
browser automations could race on the shared session file. `fetch`-vs-`fetch` dedup behavior is
unchanged; this only closes a gap that becomes reachable once `verify` exists.

## API contract

### `POST /tds/api/v1/challan/verify`

Request:

```json
{
  "tan": "PTLA13241E",
  "password": "...",
  "financialYear": "2026-27",
  "manual_challans": [
    {
      "id": "c-9231",
      "voucherNo": "37358",
      "bsrCode": "0180002",
      "dateOfDeposit": "07/05/2026",
      "totalAmount": 52830.00
    }
  ]
}
```

- `bsrCode` and `voucherNo` must be strings. `bsrCode` must be sent as a string — if the caller
  sends it as a JSON number, leading zeros are already lost before this service sees it.
- `dateOfDeposit` accepts the same formats the codebase already parses via
  `fetcher.core.enrich._parse_date_str` (`DD/MM/YYYY`, `YYYY-MM-DD`, `DD-Mon-YYYY`, etc.).
- `id` is an opaque identifier supplied by the caller (their own DB primary key), echoed back in
  the response `details`. Matching results by `voucherNo` alone is not safe — the spec's own test
  case has duplicate voucher numbers with different BSR codes — so responses key off `id`.

Empty `manual_challans` short-circuits synchronously, no job created, no TRACES call:

```json
{ "success": true, "message": "No manual challans found.", "totalManual": 0, "verified": 0, "notVerified": 0, "details": [] }
```

Otherwise, response mirrors `/fetch`'s job-creation shape:

```json
{ "job_id": "...", "status": "pending", "queue_position": 1 }
```

### Job result (via existing `GET /tds/api/v1/jobs/{job_id}`)

On `status: "completed"`, `result` is:

```json
{
  "success": true,
  "message": "Verification completed.",
  "totalManual": 12,
  "verified": 10,
  "notVerified": 2,
  "fromDate": "07/05/2026",
  "toDate": "01/07/2026",
  "governmentFetched": 47,
  "details": [
    { "id": "c-9231", "voucherNo": "37358", "status": "Verified", "matchedCrn": "26050700123452KKBK" },
    { "id": "c-9420", "voucherNo": "78945", "status": "Not Verified", "matchedCrn": null }
  ]
}
```

On `status: "failed"`, the job gains a new `"error"` field (job records don't currently carry one
on failure) with a fixed, non-leaky message — the raw exception is logged server-side only, never
returned to the caller: `"Unable to fetch challans from Income Tax portal."`

If the fetch succeeds but the portal returns zero challans for the range, the job still
completes: `"message": "No challans found on the government portal for the selected date range."`,
every manual challan marked `Not Verified`. If it succeeds with data but nothing matches:
`"message": "Verification completed. 0 challans verified."`

## Matching logic

New pure module `fetcher/services/challan_verification.py` — no I/O, no FastAPI/Selenium
dependency:

```python
def normalize_voucher(v) -> str      # str(v).strip()
def normalize_bsr(v) -> str          # str(v).strip() — no reformatting; caller owns leading zeros
def normalize_date(v) -> str         # reuses enrich._parse_date_str(), formats "%Y-%m-%d"
def normalize_amount(v) -> str       # round(float(v), 2) formatted "%.2f"
def build_key(bsr, voucher, date, amount) -> str   # "bsr|voucher|date|amount", each normalized

def verify_challans(manual: list[dict], government: list[dict]) -> dict:
    # 1. index government once: key → crn
    # 2. O(1) lookup per manual challan
    # 3. return the summary + details shape above
```

Matching fields on the government side, from the existing (unmodified) `enrich()` output:
`bsrCode`, `challanNum` (5-digit serial — matches the UI's "Voucher No" column), `tenderDt`
(falls back to `paymentDt`), `totalAmt`.

**Known limitation, inherited, not introduced here:** `enrich()` already coerces `totalAmt` to an
integer (drops paise). Amount comparison therefore effectively rounds to whole rupees on both
sides. `enrich()` is shared code the "don't change Import Challan" constraint protects, and
changing its output type risks breaking existing Excel/JSON consumers — not fixed as part of this
work.

## Error handling summary

| Condition | Behavior |
|---|---|
| `manual_challans` empty | 200, synchronous, no job, `"No manual challans found."` |
| TRACES/automation failure | job `status: "failed"`, `error: "Unable to fetch challans from Income Tax portal."`; full exception logged server-side only |
| Portal returns zero challans for range | job `status: "completed"`, all manual → Not Verified, portal-specific message |
| Government data fetched, zero matches | job `status: "completed"`, `verified: 0`, generic message |
| Normal case | job `status: "completed"`, per-item Verified/Not Verified |

## Logging

Log: total manual challans, computed from/to date, TAN (not password), number fetched from
portal, number verified, number not verified, execution time, and any automation errors (message
only, not credentials or full taxpayer payloads).

## Testing plan

`tests/test_challan_verification.py` — pure unit tests against `verify_challans()`, no FastAPI,
no Selenium, no network:

- no manual challans (also covered at the endpoint layer)
- single verified challan
- multiple verified challans
- no matches
- duplicate voucher numbers, different BSR codes — both resolve independently via composite key
- date normalization (`07/05/2026` vs `2026-05-07` vs with time-of-day suffix)
- amount normalization (`55140`, `55140.0`, `55140.00`)
- empty government list → all Not Verified

`tests/test_api_service_verify.py` — FastAPI `TestClient`, `run_fetch` mocked:

- Python/TRACES fetch failure → job fails with the fixed error message
- empty portal response → job completes with the portal-specific message
- job creation dedups against an active `fetch` job for the same TAN (and vice versa)
- empty `manual_challans` → synchronous 200, no job row created

## Idempotency

This repo persists no "verified" state — the other backend owns that. Re-running verify with the
same input is naturally idempotent here: identical inputs always produce identical output, and the
only side effect is a new job record, the same as repeat `/fetch` calls already produce today.

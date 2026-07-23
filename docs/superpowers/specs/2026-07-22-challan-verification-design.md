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

## Addendum (2026-07-23): three-way status

A follow-up spec doc ("Challan Verification API - Implementation Prompt") refined Step 4's
matching rule: a manual challan whose BSR + Voucher + Date match a government challan but whose
Amount doesn't should be reported distinctly from one whose BSR + Voucher + Date combination has
no match at all. `verify_challans()` now returns one of three per-item statuses instead of two:

- `"Verified"` — BSR + Voucher + Date + Amount all match.
- `"Amount Not Verified"` — BSR + Voucher + Date match, Amount doesn't.
- `"Not Found"` — the BSR + Voucher + Date combination has no match.

Implementation: alongside the existing full-key (`bsr|voucher|date|amount`) index, build a second
partial-key (`bsr|voucher|date`) index from the government data. A manual challan checks the full
index first; on a miss, it checks the partial index to distinguish the two failure modes. Both
indexes are built once, so lookups stay O(n) overall — no change to the algorithmic approach, just
a second cheap index.

This is an additive change to the response contract: `totalManual`, `verified`, `fromDate`,
`toDate`, `governmentFetched`, and `details[].matchedCrn` are unchanged. New fields —
`amountNotVerified` and `notFound` counts, plus each `details[]` item's `status` now being one of
the three values above instead of `"Verified"`/`"Not Verified"` — are added. `notVerified` is kept
for backward compatibility, defined as `amountNotVerified + notFound`.

The same follow-up doc restated Steps 1 and 5 (querying the DB for eligible manual challans —
excluding non-null `SectionCode` and `BookEntry = Yes` — and persisting verification status back to
the DB). Per the same decision recorded above, those remain the other backend's responsibility;
this repo's scope is unchanged (stateless matching only, no DB access).

## Addendum (2026-07-23): this service auto-fetches manual challans

The user surfaced the actual mechanism the main TaxVahan backend uses to read its own manual
challans: a REST API, `POST https://api.taxvahan.com/api/challan/fetch` (distinct from
`py-api.taxvahan.com`, the TRACES fetch this service already drives), taking
`{deductorId, financialYear, quarter, categoryId, pageNumber, pageSize}` and returning a paginated
`challanList`. This is not database access — it's an HTTP call to an already-existing API, no
different in kind from the TRACES fetch this service already performs. Given that, the "DB access"
concern from the two earlier addenda doesn't apply here: **this service now calls that API itself**,
instead of requiring the caller to build and send the `manual_challans` array.

**Request contract change (breaking):** `POST /tds/api/v1/challan/verify` drops `manual_challans`
entirely. It now takes:

```json
{
  "tan": "PTLA13241E", "password": "...",
  "deductorId": "404868", "financialYear": "2026-27", "quarter": "Q1", "categoryId": 2
}
```

`financialYear` becomes required (it's no longer TRACES-only metadata — it's a required parameter
for the `api.taxvahan.com` call too). This is a breaking change to the contract from the first
addendum, judged acceptable because nothing in production was calling it yet (only manual Swagger
testing).

**Authentication:** confirmed with the user as a fixed service key — `TAXVAHAN_API_KEY` env var,
sent as `Authorization: Bearer <key>` on every call to `api.taxvahan.com`. Added alongside
`TAXVAHAN_API_BASE` (default `https://api.taxvahan.com`) in `fetcher/utils/config.py` and
`.env.example`, following the same pattern as the existing TRACES `BASE`/`API_BASE` constants. The
actual key value is supplied at deploy time, same as TAN/password credentials already are.

**New job runner flow** (`_run_verify_job` in `api_service.py`):

```
1. taxvahan_api.fetch_manual_challans(deductorId, financialYear, quarter, categoryId)
     — new client, fetcher/core/taxvahan_api.py, paginates until totalRows is reached
2. challan_verification.filter_eligible_manual_challans(raw_challans)
     — SectionCode null/empty AND BookEntry != 'Y' (Step 1's rule), maps fields into
       the manual-challan shape verify_challans() already expects
3. If nothing eligible → job completes with "No manual challans found.", TRACES is
   never touched
4. Otherwise: compute_date_range → run_fetch (TRACES, unchanged) → verify_challans (unchanged)
```

Two distinct job-failure messages now exist, so a caller/operator can tell which system failed:
`"Unable to fetch manual challans from TaxVahan."` (step 1 failed) vs.
`"Unable to fetch challans from Income Tax portal."` (TRACES failed, unchanged from before).

**Endpoint simplification:** since eligibility can only be known after an external call, the
synchronous "no manual challans" short-circuit and the 422 unparseable-date check both moved out of
`create_verify_job` and into the job runner as job-level outcomes — the endpoint now always creates
a job (dedup/queue-capacity checks aside), consistent with `/fetch`'s existing behavior.

## Addendum (2026-07-23): token-forwarding instead of a static service key

The previous addendum specified a fixed `TAXVAHAN_API_KEY` service key, sent as
`Authorization: Bearer <key>`, confirmed with the user at the time as the intended auth model. In
a real deployment this returned `401 Unauthorized` from `api.taxvahan.com`. Root-caused by checking
the running container directly (`docker exec tds-pan-api env`): `TAXVAHAN_API_KEY` was never set,
so every call sent an empty bearer token — but that also exposed a deeper problem, that no service
key mechanism was ever confirmed to exist on the main backend's side. The only auth flow proven to
work was the browser's own authenticated session (the 200 OK captured earlier), not a static key
this service could hold.

**Decision:** switch to token-forwarding. `VerifyChallansRequest` gains a required `authToken`
field — the caller's own Authorization header value for `api.taxvahan.com` (whatever format it
already uses, e.g. `"Bearer <jwt>"`) — forwarded verbatim by
`taxvahan_api.fetch_manual_challans(auth_token=...)`. This service no longer holds or manages any
credential for that call; `TAXVAHAN_API_KEY` is removed from `config.py` and `.env.example`
entirely (not deprecated-but-kept — YAGNI, and a stale unused env var is worse than none). Only
`TAXVAHAN_API_BASE` remains as service-level config, since the base URL isn't caller-specific.

This is strictly more robust than a static key: it reuses auth already proven to work, needs no new
secret provisioned or rotated on this service, and doesn't depend on the main backend building an
auth path that may not exist.

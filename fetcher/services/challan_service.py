"""
Challan service — main orchestration layer.

Pipeline
--------
1. Fetch challan list:
      A. CDP intercepted response body   (instant — no extra request)
      B. requests.Session API call       (paginated, with retry)
      C. DOM scraper callback            (last resort, needs live driver)
2. Date-filter results
3. Smart detail-fetch gating (skip if data already complete)
4. Parallel copychallan detail fetches with ThreadPoolExecutor + circuit breaker
5. Return enriched dataset + observability stats dict
"""

import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from ..core.api    import fetch_payment_history
from ..core.api    import fetch_challan_detail as _api_fetch_detail
from ..core.enrich import enrich, filter_by_date, needs_detail_fetch

log = logging.getLogger("TDS")


# ── Circuit Breaker ───────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Stops detail API calls when the failure rate exceeds `failure_threshold`
    once at least `min_calls` have been recorded.

    This prevents a cascade of 500s when the portal is rate-limiting or down.
    """

    def __init__(self, failure_threshold: float = 0.5, min_calls: int = 5):
        self._calls     = 0
        self._failures  = 0
        self._threshold = failure_threshold
        self._min       = min_calls
        self._tripped   = False

    def record_success(self) -> None:
        self._calls += 1

    def record_failure(self) -> None:
        self._calls += 1
        self._failures += 1
        if (not self._tripped
                and self._calls >= self._min
                and self._failures / self._calls >= self._threshold):
            log.warning(
                "Circuit breaker TRIPPED — %.0f%% failures (%d/%d). "
                "Remaining detail fetches will be skipped.",
                self._failures / self._calls * 100,
                self._failures, self._calls,
            )
            self._tripped = True

    def is_tripped(self) -> bool:
        return self._tripped


# ── Response parsing ──────────────────────────────────────────────────────────

def _parse_challan_list(data: dict) -> tuple[Optional[list], int]:
    """Extract (items, total_count) from a paymenthistory API response."""
    if not data:
        return None, 0
    if data.get("successFlag"):
        pl    = data.get("paymentList", {})
        items = pl.get("content", [])
        total = pl.get("totalElements", len(items))
        log.info("Challan list: %d items (declared total: %d)", len(items), total)
        if items:
            log.debug("First item keys: %s", sorted(items[0].keys()))
        return items, total
    if isinstance(data.get("paymentList"), list):
        items = data["paymentList"]
        log.info("Challan list (direct array): %d items", len(items))
        return items, len(items)
    log.warning(
        "paymenthistory successFlag=False — %s",
        data.get("messages", "<no message>"),
    )
    return None, 0


# ── Browser-based detail fetch (fallback when API rejects payload) ────────────

_CLOSE_MODAL_JS = """
['button[aria-label="Close"]', '.mat-dialog-close', 'button.close',
 '.close-icon button', 'mat-icon[role="button"]', '.mat-mdc-dialog-close'
].forEach(function(s){
    var el = document.querySelector(s);
    if (el) el.click();
});
"""

_DEBUG_ROW_JS = """
// Return the HTML structure of first visible row for debugging
var centerRow = document.querySelector('.ag-center-cols-container div[role="row"]');
var rightRow  = document.querySelector('.ag-pinned-right-cols-container div[role="row"]');
if (!centerRow) return {error: 'no rows'};
return {
    centerCols: Array.from(centerRow.querySelectorAll('.ag-cell')).map(function(c){
        return {
            colId: c.getAttribute('col-id'),
            text: c.innerText.trim().slice(0,30),
            buttons: Array.from(c.querySelectorAll('button,a')).map(function(b){
                return b.tagName + ':' + (b.title||b.innerText||b.className).slice(0,40);
            })
        };
    }),
    rightColsHtml: rightRow ? rightRow.innerHTML.slice(0,600) : 'none'
};
"""

# Try to set page size to 25 so more rows are visible at once
_SET_PAGE_SIZE_JS = """
var selectors = [
    'select.ag-paging-page-size',
    '.ag-paging-page-size select',
    'select[aria-label*="page" i]'
];
for (var sel of selectors) {
    var el = document.querySelector(sel);
    if (!el) continue;
    var opts = Array.from(el.options).map(function(o){ return parseInt(o.value)||0; });
    var best = opts.filter(function(v){ return v >= 20; });
    if (best.length) {
        el.value = Math.min.apply(null, best);
        el.dispatchEvent(new Event('change', {bubbles:true}));
        return 'set to ' + el.value;
    }
}
// Also try Angular Material select
var mats = document.querySelectorAll('mat-select[aria-label*="page" i], mat-select');
for (var mat of mats) {
    mat.click();
    return 'mat_clicked';
}
return 'no_selector';
"""

_CLICK_NEXT_PAGE_JS = """
var btns = Array.from(document.querySelectorAll('button'));
var next = btns.find(function(b){
    return (b.getAttribute('aria-label') || '').toLowerCase().indexOf('next') !== -1 ||
           b.innerText.trim() === '>' || b.classList.contains('ag-paging-next-button') ||
           (b.querySelector('i.fa-angle-right') !== null);
});
if (next && !next.disabled && !next.classList.contains('ag-disabled')) {
    next.click(); return true;
}
return false;
"""

_FIND_AND_CLICK_CHALLAN_JS = """
var crn = arguments[0];
var cin = arguments[1];

var centerRows = Array.from(document.querySelectorAll(
    '.ag-center-cols-container div[role="row"]'
));
var rightRows = Array.from(document.querySelectorAll(
    '.ag-pinned-right-cols-container div[role="row"]'
));

for (var i = 0; i < centerRows.length; i++) {
    var row = centerRows[i];
    var txt = row.textContent;
    var match = (crn && txt.indexOf(crn) !== -1) ||
                (cin  && txt.indexOf(cin.slice(0,14)) !== -1);
    if (!match) continue;

    // 1. Try right-pinned action column first (most common location)
    if (i < rightRows.length) {
        var rBtn = rightRows[i].querySelector(
            'button, a[role="button"], span[role="button"], mat-icon-button'
        );
        if (rBtn) {
            (rBtn.closest('button') || rBtn).click();
            return 'right_pinned';
        }
    }

    // 2. Try clicking the CIN cell (often a link/button)
    var cinCell = row.querySelector(
        'div[col-id="cinNum"], div[col-id="cin"], div[col-id="challanRefNum"]'
    );
    if (cinCell) {
        var cinLink = cinCell.querySelector('a, span.link, span[class*="blue"], span[class*="click"]');
        if (cinLink) { cinLink.click(); return 'cin_link'; }
        cinCell.click();
        return 'cin_cell_click';
    }

    // 3. Try action col-id cells
    var actCell = row.querySelector(
        'div[col-id="action"] button, div[col-id="viewCopy"] button, ' +
        'div[col-id="copy"] button, div[col-id="view"] a'
    );
    if (actCell) { actCell.click(); return 'action_col'; }

    // 4. Last button in row (rightmost = typically action)
    var allBtns = Array.from(row.querySelectorAll('button:not([disabled])'));
    if (allBtns.length > 1) {
        allBtns[allBtns.length - 1].click();
        return 'last_btn_' + allBtns.length;
    }

    // Debug: return full col structure to help diagnose
    return 'debug:' + Array.from(row.querySelectorAll('.ag-cell')).map(function(c){
        var b = c.querySelector('button,a');
        return (c.getAttribute('col-id') || '?') + (b ? '['+b.tagName+']':'');
    }).join(',');
}
return 'not_found';
"""


def _browser_fetch_batch(driver, cdp_capture, summaries: list) -> dict:
    """
    Browser-based detail fetch for a batch of summaries.

    Iterates through ag-Grid pages so that challans not visible on the first
    page are still found.  Returns {crn[:14]: detail_dict} mapping.
    """
    results: dict = {}
    pending: dict = {}
    for s in summaries:
        crn = (s.get("crn") or s.get("cin", ""))[:14]
        if crn:
            pending[crn] = s

    if not pending:
        return results

    # Try to bump the grid's page-size dropdown so we see more rows at once.
    try:
        ps_result = driver.execute_script(_SET_PAGE_SIZE_JS)
        log.info("Grid page-size adjust: %s", ps_result)
        if ps_result not in ("no_selector", "mat_clicked"):
            time.sleep(1.5)
    except Exception as exc:
        log.warning("Could not adjust grid page size: %s", exc)

    # Log row structure once for debugging.
    try:
        debug = driver.execute_script(_DEBUG_ROW_JS)
        log.info("Grid row structure (first row): %s", debug)
    except Exception:
        pass

    max_pages = 20  # safety cap — 20 × 5 rows = 100 challans
    for page_num in range(max_pages):
        if not pending:
            break

        found_on_page: list = []
        for crn, summary in list(pending.items()):
            cin    = summary.get("cin", "")
            result = driver.execute_script(_FIND_AND_CLICK_CHALLAN_JS, crn, cin)
            log.info("Browser click CRN=%s (page %d): %s", crn, page_num + 1, result)

            result_str = str(result or "")
            if "not_found" in result_str or result_str.startswith("debug:"):
                continue  # not on this page — try next page

            # Clicked — wait for CDP to capture the copychallan response.
            time.sleep(2)
            detail = cdp_capture.get_response_body("/copychallan", driver, timeout=12)
            try:
                driver.execute_script(_CLOSE_MODAL_JS)
            except Exception:
                pass
            time.sleep(0.5)
            results[crn] = detail or {}
            found_on_page.append(crn)

        for crn in found_on_page:
            pending.pop(crn, None)

        if not pending:
            break

        # Navigate to the next grid page.
        try:
            has_next = driver.execute_script(_CLICK_NEXT_PAGE_JS)
            if not has_next:
                log.info("ag-Grid: no further pages after page %d", page_num + 1)
                break
            log.info("ag-Grid: moved to page %d", page_num + 2)
            time.sleep(1.5)
        except Exception as exc:
            log.warning("Next-page navigation failed: %s", exc)
            break

    for crn in pending:
        log.warning("Browser: CRN=%s not found on any grid page", crn)
        results[crn] = {}

    return results


# ── Thread-isolated detail fetch ──────────────────────────────────────────────

def _fetch_detail_safe(session, summary: dict, tan: str) -> dict:
    """Fetch one challan's detail.  Never raises so worker threads stay healthy."""
    crn = summary.get("crn") or summary.get("cin", "")[:14]
    try:
        result = _api_fetch_detail(session, summary, tan)
        if not result or not result.get("successFlag"):
            log.warning("Detail API response  CRN=%s: %s", crn,
                        result.get("messages") if result else "empty")
        return result
    except Exception as exc:
        log.warning("Detail fetch error  CRN=%s: %s", crn, exc)
        return {}


# ── Main orchestration ────────────────────────────────────────────────────────

def run(
    cfg: dict,
    session,
    driver=None,
    cdp_capture=None,
    dom_scraper: Optional[Callable] = None,
) -> tuple[list, dict]:
    """
    Orchestrate the full fetch pipeline.

    Parameters
    ----------
    cfg         : merged CONFIG dict from main.run_fetch()
    session     : authenticated requests.Session
    driver      : Selenium WebDriver — None when saved session was reused
    cdp_capture : CDPCapture instance — None when saved session was reused
    dom_scraper : callable(driver) → list  — injected by main.py

    Returns
    -------
    (enriched_challans, stats_dict)
    """
    t0  = time.time()
    tan = cfg["TAN"]

    # ── [2] Fetch challan list ─────────────────────────────────────────
    log.info("[2/4] Fetching challan list ...")
    all_challans: list = []

    # Strategy A — steal the response Angular already loaded (zero cost)
    if cdp_capture is not None and driver is not None:
        body = cdp_capture.get_response_body(
            "/paymenthistory", driver, timeout=5
        )
        if body is not None:
            items, _ = _parse_challan_list(body)
            if items:
                log.info("  Strategy A (CDP body intercept): %d challans", len(items))
                all_challans = items

    # Strategy B — paginated requests.Session API call
    if not all_challans:
        page_num = 0
        while True:
            try:
                data = fetch_payment_history(
                    session, tan,
                    cfg["FROM_DATE"], cfg["TO_DATE"],
                    page_num=page_num,
                    page_size=cfg.get("PAGE_SIZE", 10000),
                )
                items, total = _parse_challan_list(data)
                if not items:
                    break
                all_challans.extend(items)
                log.info(
                    "  Strategy B page %d: %d / %d challans",
                    page_num, len(all_challans), total,
                )
                if len(all_challans) >= total:
                    break
                page_num += 1
                time.sleep(0.5)
            except Exception as exc:
                log.error("  Strategy B API call failed: %s", exc)
                break

    # Strategy C — DOM scraper (last resort, requires live browser)
    if not all_challans and dom_scraper is not None and driver is not None:
        log.warning("  Strategy C: falling back to DOM scraper ...")
        all_challans = dom_scraper(driver) or []
        log.info("  Strategy C (DOM): %d challans", len(all_challans))

    if not all_challans:
        log.warning("No challans found for %s–%s", cfg["FROM_DATE"], cfg["TO_DATE"])
        return [], {
            "duration":            round(time.time() - t0, 2),
            "count":               0,
            "detail_total":        0,
            "detail_success":      0,
            "detail_success_rate": 100.0,
        }

    import json
    for item in all_challans:
        jstr = item.get("jsonData")
        if jstr and isinstance(jstr, str):
            try:
                jdata = json.loads(jstr)
                for k, v in jdata.items():
                    # Only overwrite if v is non-empty and target is empty
                    if v not in (None, "") and item.get(k) in (None, "", 0):
                        item[k] = v
                    elif k not in item:
                        item[k] = v
            except:
                pass

    all_challans = filter_by_date(all_challans, cfg["FROM_DATE"], cfg["TO_DATE"])

    log.info("Challans after date filter: %d", len(all_challans))

    if not all_challans:
        log.warning("No challans remain after date filter.")
        return [], {
            "duration":            round(time.time() - t0, 2),
            "count":               0,
            "detail_total":        0,
            "detail_success":      0,
            "detail_success_rate": 100.0,
        }

    if all_challans:
        log.debug("DEBUG: First challan raw summary: %s", all_challans[0])

    # ── Normalise CRN field ────────────────────────────────────────────

    first     = all_challans[0]
    crn_field = next(
        (f for f in ("crn", "challanRefNum", "challanReferenceNumber",
                     "challanRefNo", "referenceNumber")
         if first.get(f)),
        "crn",
    )
    log.info(
        "CRN field: '%s'  (example value: %s)",
        crn_field, first.get(crn_field, "<empty>"),
    )
    for item in all_challans:
        item["crn"] = item.get(crn_field) or item.get("crn", "")


    # ── [3] Smart detail fetch ─────────────────────────────────────────
    detail_mode = cfg.get("DETAIL_MODE", cfg.get("DETAIL_FETCH", "AUTO"))
    log.info("[3/4] Detail fetch mode: %s", detail_mode)

    enriched: list    = []
    jobs_to_run: list = []

    for summary in all_challans:
        crn = summary["crn"]
        if not crn or detail_mode == "SKIP":
            enriched.append(enrich(summary, {}))
        elif detail_mode == "ALWAYS" or needs_detail_fetch(summary):
            jobs_to_run.append(summary)
        else:
            log.debug("  Skip detail (already complete): CRN %s", crn)
            enriched.append(enrich(summary, {}))

    detail_total   = len(jobs_to_run)
    detail_success = 0
    cb             = CircuitBreaker(failure_threshold=0.5, min_calls=5)
    workers        = cfg.get("WORKERS", 15)
    batch_size     = cfg.get("BATCH_SIZE", 5)

    log.info(
        "  Detail fetches queued: %d / %d  (%d already complete)",
        detail_total, len(all_challans), len(enriched),
    )

    # Track summaries that still need detail after API phase
    api_failed: list = []

    for batch_start in range(0, detail_total, batch_size):
        batch          = jobs_to_run[batch_start:batch_start + batch_size]
        actual_workers = min(workers, len(batch))

        with ThreadPoolExecutor(max_workers=actual_workers) as pool:
            future_map: dict = {}

            for summary in batch:
                if cb.is_tripped():
                    api_failed.append(summary)
                    continue
                fut = pool.submit(
                    _fetch_detail_safe, session, summary, tan
                )
                future_map[fut] = summary

            for fut in as_completed(future_map):
                summary = future_map[fut]
                crn     = summary["crn"]
                try:
                    detail = fut.result()
                    has_data = bool(detail and (
                        detail.get("successFlag")
                        or detail.get("challanNum")
                        or detail.get("bsrCode")
                        or detail.get("basicTax") is not None
                    ))
                    if has_data:
                        cb.record_success()
                        detail_success += 1
                        log.info(
                            "  [%d/%d] Detail OK  CRN=%s",
                            detail_success, detail_total, crn,
                        )
                        enriched.append(enrich(summary, detail))
                    else:
                        cb.record_failure()
                        log.warning("  Detail EMPTY  CRN=%s", crn)
                        api_failed.append(summary)
                except Exception as exc:
                    cb.record_failure()
                    log.error("  Detail ERROR  CRN=%s: %s", crn, exc)
                    api_failed.append(summary)

    # ── Browser-based fallback (when API rejected all requests) ───────
    if api_failed and driver is not None and cdp_capture is not None:
        log.info(
            "[3b/4] Browser-based detail fetch for %d failed challans ...",
            len(api_failed),
        )
        browser_results = _browser_fetch_batch(driver, cdp_capture, api_failed)
        for summary in api_failed:
            crn    = (summary.get("crn") or summary.get("cin", ""))[:14]
            detail = browser_results.get(crn, {})
            has_data = bool(detail and (
                detail.get("successFlag")
                or detail.get("challanNum")
                or detail.get("bsrCode")
            ))
            if has_data:
                detail_success += 1
                log.info("  Browser detail OK  CRN=%s", crn)
                enriched.append(enrich(summary, detail))
            else:
                log.warning("  Browser detail EMPTY  CRN=%s", crn)
                enriched.append(enrich(summary, {}))
    else:
        # No browser available or no failures — just enrich with empty detail
        for summary in api_failed:
            enriched.append(enrich(summary, {}))

    # ── Observability stats ────────────────────────────────────────────
    duration     = round(time.time() - t0, 2)
    success_rate = (detail_success / detail_total * 100) if detail_total else 100.0

    stats = {
        "duration":            duration,
        "count":               len(enriched),
        "detail_total":        detail_total,
        "detail_success":      detail_success,
        "detail_success_rate": round(success_rate, 1),
    }

    log.info(
        "Challan service complete: %d challans | details %d/%d (%.1f%%) | %.1f s",
        len(enriched), detail_success, detail_total, success_rate, duration,
    )
    return enriched, stats

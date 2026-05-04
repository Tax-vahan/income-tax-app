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

from selenium.webdriver.common.by import By

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
 '.close-icon button', 'mat-icon[role="button"]', '.mat-mdc-dialog-close',
 '#securityReasonPopup button', '.modal-footer button'
].forEach(function(s){
    var el = document.querySelector(s);
    if (el) el.click();
});
// Also try generic 'Ok' or 'Close' text
Array.from(document.querySelectorAll('button')).forEach(function(b){
    var t = b.innerText.trim().toLowerCase();
    if (t === 'ok' || t === 'close' || t === 'dismiss') b.click();
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

_FIND_ROW_AND_CLICK_MENU_JS = """
var crn = arguments[0];
var cin = arguments[1];
var rows = Array.from(document.querySelectorAll('.ag-center-cols-container div[role="row"]'));
for (var row of rows) {
    if (row.textContent.indexOf(crn) === -1 && (!cin || row.textContent.indexOf(cin.slice(0,14)) === -1)) continue;
    var btn = row.querySelector('button .mat-icon, button mat-icon');
    if (!btn) btn = Array.from(row.querySelectorAll('button')).find(b => b.innerText.indexOf('more_vert') !== -1);
    if (btn) {
        var clickBtn = btn.closest('button') || btn;
        clickBtn.scrollIntoView({block:'center'});
        clickBtn.click();
        return 'clicked_vert';
    }
}
return 'not_found';
"""

_CLICK_MENU_OPTION_JS = """
var targetText = arguments[0].toLowerCase();
var startTime = Date.now();
function findAndClick() {
    var items = Array.from(document.querySelectorAll('button, [role="menuitem"], .mat-mdc-menu-item, .mat-menu-item, .mat-menu-panel button, .cdk-overlay-container button'));
    var found = items.find(function(item) {
        var t = (item.innerText || "").toLowerCase();
        return t.indexOf(targetText) !== -1;
    });
    if (found) {
        found.click();
        return 'clicked:' + found.innerText.trim();
    }
    return null;
}

// Try once
var res = findAndClick();
if (res) return res;

// If not found, it might be an overlay still opening. But since we are in execute_script, we can't wait easily.
// Let's try one last global search
var globalItems = Array.from(document.querySelectorAll('button'));
var globalTarget = globalItems.find(function(b){
    return b.innerText.trim().toLowerCase() === targetText;
});
if (globalTarget) {
    globalTarget.click();
    return 'clicked_global:' + globalTarget.innerText.trim();
}
return 'not_found';
"""

_SCRAPE_VIEW_DETAILS_JS = r"""

var data = {successFlag: true};
var labels = document.querySelectorAll('label, .mat-label, .details-label');
labels.forEach(function(l) {
    var txt = l.innerText.trim().toLowerCase();
    var val = (l.nextElementSibling ? l.nextElementSibling.innerText.trim() : '');
    if (!val) {
        var parent = l.closest('.row') || l.parentElement;
        var valEl = parent.querySelector('.details-value, span, b');
        if (valEl) val = valEl.innerText.trim();
    }
    if (txt.indexOf('bsr code') !== -1) data.bsrCode = val;
    if (txt.indexOf('challan number') !== -1) data.challanNum = val;
    if (txt.indexOf('alternate cin') !== -1) data.alternateCin = val;
    if (txt.indexOf('tender date') !== -1) data.tenderDt = val;
});
// Fallback: look for BSR-like 7-digit numbers if bsrCode still missing
if (!data.bsrCode) {
    var allText = document.body.innerText;
    var bsrMatch = allText.match(/BSR Code[:\s]+(\d{7})/i);
    if (bsrMatch) data.bsrCode = bsrMatch[1];
}
return Object.keys(data).length > 1 ? data : null;
"""

_SCRAPE_BREAKDOWN_JS = """
var amounts = {successFlag: true};
var fields = document.querySelectorAll('mat-form-field');
var found = false;
fields.forEach(function(f) {
    var label = f.querySelector('mat-label');
    if (!label) return;
    var txt = label.innerText.trim().toLowerCase();
    var input = f.querySelector('input');
    if (!input) return;
    var val = parseFloat(input.value.replace(/,/g, '')) || 0;
    
    if (txt.indexOf('basic tax') !== -1 || txt === 'tax') { amounts.basicTax = val; found = true; }
    if (txt.indexOf('surcharge') !== -1) { amounts.surCharge = val; found = true; }
    if (txt.indexOf('cess') !== -1) { amounts.eduCess = val; found = true; }
    if (txt.indexOf('interest') !== -1) { amounts.interest = val; found = true; }
    if (txt.indexOf('penalty') !== -1) { amounts.penalty = val; found = true; }
    if (txt.indexOf('others') !== -1) { amounts.others = val; found = true; }
});
return found ? amounts : null;
"""

_CONTINUE_IF_NATURE_PAGE_JS = """
if (window.location.href.indexOf('tds-payable-by-taxpayer') !== -1) {
    var btn = Array.from(document.querySelectorAll('button')).find(function(b){
        return b.innerText.indexOf('Continue') !== -1;
    });
    if (btn) { btn.click(); return 'clicked_continue'; }
}
return 'not_nature_page';
"""


def _browser_fetch_batch(driver, cdp_capture, summaries: list) -> dict:
    """
    Browser-based detail fetch using CDP response-body interception.

    Strategy (per challan):
      1. Scroll the ag-Grid to bring the target row into the virtualised DOM.
      2. Click the row's more_vert menu → "Copy".
      3. The browser fires its own authenticated copychallan API call.
         CDP captures that request & response with the full Angular auth headers.
      4. Call cdp_capture.get_response_body("/copychallan", body_contains=crn)
         to steal the JSON the browser just received — no manual auth needed.
      5. Navigate back, repeat for next challan.

    This bypasses the X-XSRF-TOKEN problem entirely: the browser's own Angular
    requests always carry the correct XSRF and AuthToken headers.

    Returns {crn_14_digits: detail_dict} mapping.
    """
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    results: dict = {}
    pending: dict = {}
    for s in summaries:
        crn = (s.get("crn") or str(s.get("cin", ""))[:14])
        if crn:
            pending[crn] = s

    if not pending:
        return results

    epay_url = "https://eportal.incometax.gov.in/iec/foservices/#/e-pay-tax"

    _GRID_HAS_ROWS_JS = (
        "return document.querySelectorAll("
        "  '.ag-center-cols-container div[role=\"row\"]').length > 0;"
    )

    def _wait_for_grid_rows(timeout: int = 25) -> bool:
        """Wait for ag-Grid rows to appear. Returns True on success."""
        W = WebDriverWait(driver, timeout)
        try:
            W.until(lambda d: d.execute_script(_GRID_HAS_ROWS_JS))
            # Remove any CDK overlay that blocks row interaction
            driver.execute_script(
                "document.querySelectorAll('.cdk-overlay-container,"
                ".cdk-overlay-backdrop,.mat-dialog-container,"
                ".mat-mdc-dialog-container').forEach(function(n){n.remove();});"
            )
            return True
        except Exception:
            return False

    def _click_history_tab() -> bool:
        """Click the Payment History tab. Returns True if found and clicked."""
        W = WebDriverWait(driver, 15)
        try:
            tab = W.until(EC.element_to_be_clickable(
                (By.XPATH,
                 "//div[@role='tab']//span[contains(text(),'Payment History')]")
            ))
            driver.execute_script("arguments[0].click();", tab)
            return True
        except Exception:
            pass
        for el in driver.find_elements(
                By.XPATH, "//span[contains(text(),'Payment History')]"):
            try:
                driver.execute_script("arguments[0].click();", el)
                return True
            except Exception:
                pass
        return False

    def _reload_payment_history(target_page: int = 0) -> bool:
        """
        Navigate to e-pay-tax, handle the Act-selection dialog, click Payment
        History tab, wait for rows, then advance to target_page (0-based).
        Returns True when rows are visible.
        """
        driver.get(epay_url)
        time.sleep(4)
        # Handle "Select Applicable Income Tax Act" dialog if it appears
        W = WebDriverWait(driver, 10)
        for _ in range(2):
            try:
                if "Select Applicable Income Tax Act" not in driver.page_source:
                    break
                act_radio = W.until(EC.presence_of_element_located(
                    (By.XPATH,
                     "//label[contains(.,'Income-tax Act, 1961')]")
                ))
                driver.execute_script("arguments[0].click();", act_radio)
                time.sleep(1)
                cont_btn = W.until(EC.presence_of_element_located(
                    (By.XPATH,
                     "//button[contains(@class,'large-button-primary')"
                     " and normalize-space()='Continue']")
                ))
                driver.execute_script("arguments[0].click();", cont_btn)
                time.sleep(5)
            except Exception as exc:
                log.debug("Act dialog handling: %s", exc)
                break

        _click_history_tab()
        ok = _wait_for_grid_rows(30)
        if not ok:
            log.warning("Grid rows never appeared after navigating to Payment History")

        # Advance to target page
        for i in range(target_page):
            try:
                has_next = driver.execute_script(_CLICK_NEXT_PAGE_JS)
                if not has_next:
                    log.warning("Ran out of pages reaching page %d", target_page + 1)
                    break
                time.sleep(2)
            except Exception as exc:
                log.warning("Page-advance error at step %d: %s", i + 1, exc)
                break
        return ok

    # ── Initial setup: ensure grid has rows ───────────────────────────────
    try:
        log.info("Browser fetch: ensuring Payment History grid is loaded ...")
        driver.execute_script(_CLOSE_MODAL_JS)
        time.sleep(0.5)

        if driver.execute_script(_GRID_HAS_ROWS_JS):
            log.info("  Grid already loaded")
        else:
            log.info("  No rows yet — clicking Payment History tab ...")
            _click_history_tab()
            if not _wait_for_grid_rows(20):
                log.warning("  Still no rows — falling back to full navigation ...")
                _reload_payment_history(0)
    except Exception as exc:
        log.warning("Browser fetch setup failed: %s", exc)

    # Try to set a larger page size so more challans fit on one page
    try:
        ps_result = driver.execute_script(_SET_PAGE_SIZE_JS)
        if ps_result not in ("no_selector", None):
            log.info("Grid page size set: %s — waiting for reload ...", ps_result)
            time.sleep(3)
    except Exception:
        pass

    # ── Log first-row structure once for debugging ────────────────────────
    try:
        debug = driver.execute_script(_DEBUG_ROW_JS)
        log.info("Grid first-row structure: %s", debug)
    except Exception:
        pass

    # ── JavaScript to scroll ag-Grid to the row containing a search string ─
    _SCROLL_ROW_INTO_VIEW_JS = """
var searchStr = arguments[0];
var viewport = document.querySelector('.ag-body-viewport') ||
               document.querySelector('.ag-center-cols-viewport');
if (!viewport) return 'no_viewport';
// Try to find existing row first
var rows = Array.from(document.querySelectorAll('.ag-center-cols-container div[role="row"]'));
for (var r of rows) {
    if (r.textContent.indexOf(searchStr) !== -1) return 'already_visible';
}
// Scroll down in steps to trigger virtual row rendering
var step = viewport.clientHeight * 0.8;
var maxScroll = viewport.scrollHeight;
var scrollPos = 0;
while (scrollPos < maxScroll) {
    scrollPos += step;
    viewport.scrollTop = scrollPos;
    // Check if our row appeared
    rows = Array.from(document.querySelectorAll('.ag-center-cols-container div[role="row"]'));
    for (var r of rows) {
        if (r.textContent.indexOf(searchStr) !== -1) return 'scrolled_found';
    }
}
viewport.scrollTop = 0;
return 'not_found_after_scroll';
"""

    grid_page = 0   # 0-based index of the currently-visible grid page
    max_pages = 50

    for page_num in range(max_pages):
        if not pending:
            break

        found_on_page: list = []

        for crn, summary in list(pending.items()):
            detail = {}
            try:
                # ── Step 1: Scroll ag-Grid to make the target row visible ─
                scroll_res = driver.execute_script(_SCROLL_ROW_INTO_VIEW_JS, crn)
                log.info("  Scroll result for CRN=%s: %s", crn, scroll_res)
                time.sleep(0.5)

                # ── Step 2: Find the row element ──────────────────────────
                xpath = (
                    f"//div[contains(@class,'ag-center-cols-container')]"
                    f"//div[@role='row' and contains(.,'{crn}')]"
                )
                rows = driver.find_elements(By.XPATH, xpath)
                if not rows:
                    log.warning("  Row not in DOM for CRN=%s (page %d)",
                                crn, grid_page + 1)
                    continue

                row = rows[0]
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", row)
                time.sleep(0.3)

                # ── Step 3: Click more_vert menu button in this row ───────
                menu_btns = row.find_elements(By.XPATH,
                    ".//button[.//mat-icon[normalize-space()='more_vert'] or "
                    ".//span[normalize-space()='more_vert']]"
                )
                if not menu_btns:
                    menu_btns = row.find_elements(By.TAG_NAME, "button")

                if not menu_btns:
                    log.warning("  No action button in row for CRN=%s", crn)
                    continue

                driver.execute_script("arguments[0].click();", menu_btns[0])
                log.info("  Clicked more_vert for CRN=%s", crn)
                time.sleep(1.5)

                # ── Step 4: Click 'Copy' in the dropdown menu ─────────────
                copy_result = driver.execute_script(_CLICK_MENU_OPTION_JS, "Copy")
                log.info("  Copy click result for CRN=%s: %s", crn, copy_result)

                if "clicked" not in str(copy_result):
                    driver.execute_script(_CLOSE_MODAL_JS)
                    continue

                # ── Step 5: Intercept the CDP response body ───────────────
                log.info("  Waiting for CDP copychallan response for CRN=%s ...", crn)
                cdp_detail = cdp_capture.get_response_body(
                    "/copychallan", driver,
                    timeout=15,
                    body_contains=crn,
                )

                if cdp_detail and cdp_detail.get("successFlag"):
                    log.info("  CDP copychallan OK for CRN=%s: bsr=%s challanNum=%s",
                             crn,
                             cdp_detail.get("bsrCode") or cdp_detail.get("bsrCd"),
                             cdp_detail.get("challanNum"))
                    detail.update(cdp_detail)
                else:
                    log.warning("  CDP copychallan empty/failed for CRN=%s: %s",
                                crn, cdp_detail)

                # ── Step 6: Handle "nature of payment" page if portal navigated ─
                time.sleep(2)
                nav_res = driver.execute_script(_CONTINUE_IF_NATURE_PAGE_JS)
                if "clicked" in str(nav_res):
                    log.info("  Bypassed nature-of-payment page for CRN=%s", crn)
                    time.sleep(3)
                    breakdown = driver.execute_script(_SCRAPE_BREAKDOWN_JS)
                    if breakdown and not detail.get("basicTax"):
                        log.info("  DOM breakdown fallback for CRN=%s: tax=%s",
                                 crn, breakdown.get("basicTax"))
                        detail.update(breakdown)

                # ── Step 7: Return to Payment History at the same page ────
                # Only navigate away if the browser left the e-pay-tax page.
                if epay_url.split("#")[0] not in driver.current_url:
                    _reload_payment_history(grid_page)
                else:
                    # Still on e-pay-tax — just re-click the tab and wait
                    _click_history_tab()
                    _wait_for_grid_rows(20)

            except Exception as exc:
                log.warning("  Browser extraction error CRN=%s: %s", crn, exc)
                try:
                    _reload_payment_history(grid_page)
                except Exception:
                    pass

            results[crn] = detail
            found_on_page.append(crn)

        for crn in found_on_page:
            pending.pop(crn, None)

        if not pending:
            break

        # Advance to next grid page
        try:
            has_next = driver.execute_script(_CLICK_NEXT_PAGE_JS)
            if not has_next:
                log.info("ag-Grid: no further pages after page %d", grid_page + 1)
                break
            grid_page += 1
            log.info("ag-Grid: advanced to page %d", grid_page + 1)
            time.sleep(2)
        except Exception as exc:
            log.warning("Next-page click failed: %s", exc)
            break

    for crn in pending:
        log.warning("Browser: CRN=%s not found on any grid page — giving up", crn)
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
                # Unpack EVERYTHING from jsonData — it's usually the most accurate
                for k, v in jdata.items():
                    if v in (None, ""): continue
                    
                    # Special case: if we have a short challanNum in jsonData, it's better than the CRN
                    if k == "challanNum":
                        old_val = str(item.get("challanNum", ""))
                        new_val = str(v)
                        if len(new_val) < len(old_val) or len(old_val) >= 14:
                            item[k] = new_val
                    # Overwrite if target is empty or 0
                    elif item.get(k) in (None, "", 0):
                        item[k] = v
                    elif k not in item:
                        item[k] = v
                        
                # Ensure we have assessmentYear if it was misspelled as assmentYear
                if not item.get("assessmentYear") and item.get("assmentYear"):
                    item["assessmentYear"] = item["assmentYear"]
            except Exception as e:
                log.debug("Failed to unpack jsonData for CRN %s: %s", item.get("crn"), e)


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
    # The portal's paymenthistory response uses "cin" as the primary identifier,
    # formatted as "<14-digit-CRN><4-letter-bank-code>" e.g. "26041100002738KKBK".
    # We need to extract just the 14-digit CRN prefix for API calls.

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
        crn_val = item.get(crn_field) or item.get("crn", "")

        # If crn_val is empty but cin has the "CRNKKBK" pattern, extract CRN from it
        if not crn_val:
            raw_cin = str(item.get("cin") or "").strip()
            if len(raw_cin) > 14 and raw_cin[:14].isdigit():
                crn_val = raw_cin[:14]
                log.debug("CRN extracted from cin='%s' → crn='%s'", raw_cin, crn_val)

        item["crn"] = crn_val


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

    log.info(
        "  Detail fetches queued: %d / %d  (%d already complete)",
        detail_total, len(all_challans), len(enriched),
    )

    # Track summaries that still need detail after API phase
    api_failed: list = []

    if jobs_to_run:
        actual_workers = min(workers, detail_total)
        with ThreadPoolExecutor(max_workers=actual_workers) as pool:
            future_map: dict = {}

            for summary in jobs_to_run:
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

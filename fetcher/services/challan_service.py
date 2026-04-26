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

    for batch_start in range(0, detail_total, batch_size):
        batch          = jobs_to_run[batch_start:batch_start + batch_size]
        actual_workers = min(workers, len(batch))

        with ThreadPoolExecutor(max_workers=actual_workers) as pool:
            future_map: dict = {}

            for summary in batch:
                if cb.is_tripped():
                    enriched.append(enrich(summary, {}))
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
                    if detail and detail.get("successFlag"):
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
                        enriched.append(enrich(summary, {}))
                except Exception as exc:
                    cb.record_failure()
                    log.error("  Detail ERROR  CRN=%s: %s", crn, exc)
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

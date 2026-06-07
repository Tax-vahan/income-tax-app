"""
TDS Challan Fetcher  v6.0  —  Package entry point

Public interface (used by api_service.py and the CLI):
    from fetcher.main import run_fetch
    run_fetch(cfg_override)  →  (output_path, record_count, grand_total)

CLI usage (from tds_api/ directory):
    python -m fetcher.main --tan XXXX --password YYYY --from-date 01/01/2026 --to-date 31/03/2026

Architecture
    utils/config.py          shared constants + CONFIG dict
    utils/logger.py          logging setup
    core/cdp.py              CDP Network event capture
    core/auth.py             Selenium login + session save/load
    core/api.py              requests.Session API calls (with retry)
    core/enrich.py           data normalisation + date filtering
    services/challan_service.py  orchestration + parallel detail fetch
    services/excel_service.py    .xlsx workbook generation
"""

import json
import sys
import logging
import argparse
import time

from .utils.config              import CONFIG
from .utils.logger              import setup as _setup_logging
from .core                      import auth
from .core.api                  import fetch_entity_profile
from .services                  import challan_service, excel_service

# ── Logging (configured once at import time) ──────────────────────────────────
_setup_logging()
log = logging.getLogger("TDS")


# ── DOM scraper (last-resort fallback — needs a live Selenium driver) ─────────

def _scrape_dom(driver) -> list:
    """
    Precision ag-Grid scraper.  Reads the Payment History grid page-by-page
    via JavaScript.  Called only when both CDP body and API calls have failed.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    log.info("DOM scraper: starting ag-Grid extraction ...")
    all_items: list = []
    seen_uids: set  = set()
    W = WebDriverWait(driver, 25)

    try:
        W.until(lambda d: len(
            d.find_elements(By.CSS_SELECTOR, "div[role='gridcell']")
        ) > 3)

        page = 1
        while True:
            log.info("  DOM scraper page %d ...", page)
            rows = driver.execute_script("""
                return Array.from(
                    document.querySelectorAll(
                        '.ag-center-cols-container div[role="row"]'
                    )
                ).map(r => {
                    var d = {};
                    r.querySelectorAll('div.ag-cell').forEach(c => {
                        var id = c.getAttribute('col-id');
                        if (id) d[id] = c.innerText.trim();
                    });
                    return d;
                });
            """) or []

            if not rows:
                log.warning("    No rows on page %d — stopping", page)
                break

            if page == 1:
                log.info("DOM columns: %s", list(rows[0].keys()))

            first_cin = rows[0].get("cinNum") or rows[0].get("crn", "")
            added = 0
            for d in rows:
                crn = (d.get("crn") or d.get("challanRefNum")
                       or d.get("challanReferenceNumber")
                       or d.get("challanRefNo") or "")
                cin = d.get("cinNum") or d.get("cin") or ""
                uid = crn or cin
                if not uid or uid in seen_uids:
                    continue
                all_items.append({
                    "crn":         crn,
                    "cin":         cin,
                    "paymentDate": d.get("paymentDate") or "",
                    "sectionName": d.get("typeOfPayment") or "",
                    "amount":      d.get("amount") or "0",
                    "status":      "Success",
                })
                seen_uids.add(uid)
                added += 1

            log.info("    Page %d: +%d items (total %d)", page, added, len(all_items))

            clicked = driver.execute_script("""
                var btns = Array.from(document.querySelectorAll('button.buttonPag'));
                var next = btns.find(b =>
                    b.innerText.includes('Next') || b.querySelector('i.fa-angle-right')
                );
                if (!next) next = document.querySelector('button.ag-paging-next-button');
                if (next && !next.disabled && !next.classList.contains('disabled')) {
                    next.click(); return true;
                }
                return false;
            """)
            if not clicked:
                log.info("  DOM scraper: reached last page")
                break

            try:
                W.until(lambda d: d.execute_script(
                    "var r = document.querySelector("
                    "  '.ag-center-cols-container div[role=\"row\"]"
                    "   div[col-id=\"cinNum\"]'"
                    ");"
                    f"return r && r.innerText.trim() !== '{first_cin}';"
                ))
            except Exception:
                break

            page += 1
            time.sleep(2)

    except Exception as exc:
        log.error("DOM scraper failed: %s", exc)
        try:
            driver.save_screenshot("scrape_error.png")
        except Exception:
            pass

    log.info("DOM scraper done: %d challans", len(all_items))
    return all_items


# ── Entity profile helper ─────────────────────────────────────────────────────

_EPOCH_FIELDS = (
    "createdTmstmp", "lastUpdatedTmstmp", "lastLoginTmstmp",
    "lastLogoutTmstmp", "tanAllotDt", "contactDob",
)


def _save_entity_profile(profile: dict, cfg: dict) -> None:
    """
    Normalise epoch-ms timestamps to ISO strings, then write the profile
    to  data/<TAN>_entity_profile.json  (same data dir as the session file).
    """
    from datetime import datetime, timezone
    import os

    out = profile.copy()
    for field in _EPOCH_FIELDS:
        v = out.get(field)
        if v is not None:
            try:
                out[field] = datetime.fromtimestamp(
                    float(v) / 1000, tz=timezone.utc
                ).strftime("%d/%m/%Y %H:%M:%S UTC")
            except Exception:
                pass

    data_dir  = os.path.dirname(cfg.get("SESSION_FILE", "data/s.json"))
    json_path = os.path.join(data_dir, f"{cfg['TAN']}_entity_profile.json")
    os.makedirs(data_dir, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    log.info("Entity profile saved → %s", json_path)


# ── Main entry point ──────────────────────────────────────────────────────────

def run_fetch(cfg_override: dict | None = None) -> tuple:
    """
    Full pipeline entry point.

    Called by api_service.py for every background job, and by the CLI.
    Returns (output_path, record_count, grand_total).
    Raises on fatal errors (login failure, empty result, etc.).
    """
    cfg = CONFIG.copy()
    if cfg_override:
        cfg.update(cfg_override)

    if not cfg.get("PASSWORD"):
        raise ValueError("PASSWORD is not set in config")

    t0 = time.time()
    log.info("=" * 62)
    log.info("TDS Challan Fetcher v6.0")
    log.info("TAN: %s   Period: %s → %s",
             cfg["TAN"], cfg["FROM_DATE"], cfg["TO_DATE"])
    log.info("=" * 62)

    # ── [1] Session — reuse saved or fresh Selenium login ─────────────
    log.info("[1/4] Establishing session ...")
    session, driver, cdp_capture = auth.get_session(cfg)
    if session is None:
        raise Exception("Session establishment failed")

    # ── [1b] Entity profile ───────────────────────────────────────────
    # The portal calls /saveEntity when the dashboard loads.  If CDP was
    # running during that load, the response body is already cached — read
    # it directly instead of making a new (potentially 403-ing) API call.
    try:
        profile = None
        if cdp_capture is not None and driver is not None:
            profile = cdp_capture.get_response_body("/saveEntity", driver, timeout=5)
        if profile is None:
            profile = fetch_entity_profile(session, cfg["TAN"])
        if profile:
            _save_entity_profile(profile, cfg)
    except Exception as exc:
        log.warning("Entity profile fetch failed (non-fatal): %s", exc)

    try:
        # ── [2+3] Challan list + parallel detail fetch ─────────────────
        enriched, stats = challan_service.run(
            cfg, session, driver, cdp_capture,
            dom_scraper=_scrape_dom,
        )
    finally:
        # Always release browser resources
        if cdp_capture:
            cdp_capture.stop()
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
            log.info("Browser closed")

    if not enriched:
        log.warning("No challans found in the specified period.")
        # Proceed to write an empty excel so the user gets a file with run info


    # ── [4] Write outputs ──────────────────────────────────────────────
    log.info("[4/4] Writing output files ...")
    out_path, grand = excel_service.write_excel(enriched, cfg, stats)

    # JSON sidecar (same base name as the xlsx)
    json_path = out_path.rsplit(".", 1)[0] + ".json"
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(enriched, f, indent=2, default=str)
        log.info("JSON saved → %s", json_path)
    except Exception as exc:
        log.error("JSON write failed: %s", exc)

    total_time = time.time() - t0
    log.info(
        "DONE  %d records | grand total %s | %.1f s",
        len(enriched), f"{grand:,.0f}", total_time,
    )
    log.info(
        "Detail fetch: %d/%d (%.1f%%)",
        stats["detail_success"], stats["detail_total"],
        stats["detail_success_rate"],
    )
    return out_path, len(enriched), grand


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="TDS Challan Fetcher v6.0",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--tan",            help="TAN Number")
    p.add_argument("--password",       help="Portal password")
    p.add_argument("--from-date",      metavar="DD/MM/YYYY", help="From date")
    p.add_argument("--to-date",        metavar="DD/MM/YYYY", help="To date")
    p.add_argument("--workers",        type=int, help="Parallel workers for detail fetch")
    p.add_argument("--batch-size",     type=int, help="Detail fetch batch size")
    p.add_argument(
        "--detail-mode",
        choices=["AUTO", "ALWAYS", "SKIP"],
        help="AUTO: fetch only when needed  |  ALWAYS: always  |  SKIP: never",
    )
    p.add_argument("--output",         metavar="FILE.xlsx", help="Output file path")
    p.add_argument("--proxy",          help="Proxy URL (e.g. socks5://127.0.0.1:1080)")
    p.add_argument(
        "--no-session-reuse",
        action="store_true",
        help="Force fresh Selenium login even if a saved session exists",
    )
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()

    override: dict = {}
    if args.tan:              override["TAN"]         = args.tan
    if args.password:         override["PASSWORD"]    = args.password
    if args.from_date:        override["FROM_DATE"]   = args.from_date
    if args.to_date:          override["TO_DATE"]     = args.to_date
    if args.workers:          override["WORKERS"]     = args.workers
    if args.batch_size:       override["BATCH_SIZE"]  = args.batch_size
    if args.detail_mode:      override["DETAIL_MODE"] = args.detail_mode
    if args.output:           override["OUTPUT_FILE"] = args.output
    if args.proxy:            override["PROXY"]       = args.proxy
    if args.no_session_reuse: override["SESSION_TTL"] = 0

    try:
        run_fetch(override)
    except Exception as exc:
        log.exception("Fatal error: %s", exc)
        sys.exit(1)

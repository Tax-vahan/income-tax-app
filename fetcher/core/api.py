"""
ITD portal API layer — all network calls go through here.

Rules
-----
- No Selenium.  Every function receives a requests.Session.
- Every public function is wrapped with @with_retry so callers get
  automatic backoff without any extra code.
- Returns the raw JSON dict; parsing / validation is the caller's job.
"""

import logging
import requests

from ..utils.config import API_BASE, CONFIG
from .retry import with_retry

log = logging.getLogger("TDS")


# ── Internal POST helper ───────────────────────────────────────────────────────

def _post(
    session: requests.Session,
    url:     str,
    payload: dict,
    timeout: int = 30,
) -> dict:
    resp = session.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ── Public API functions ───────────────────────────────────────────────────────

@with_retry(max_retries=CONFIG["RETRY_COUNT"], base_delay=1.0)
def fetch_payment_history(
    session:   requests.Session,
    tan:       str,
    from_date: str,
    to_date:   str,
    page_num:  int = 0,
    page_size: int = 10000,
) -> dict:
    """
    Paginated challan list.
    Response shape: {successFlag, paymentList: {content: [...], totalElements}}
    """
    url = (
        API_BASE
        + "/paymentapi/auth/challan/paymenthistory"
        + f"?pageNum={page_num}&size={page_size}"
    )
    payload = {
        "header": {"formName": "PO-03-PYMNT"},
        "formData": {
            "pan":            tan,
            "actType":        "O",
            "loggedInUserID": tan,
            "fromDate":       from_date,
            "toDate":         to_date,
        },
    }
    return _post(session, url, payload, timeout=CONFIG["TIMEOUT"])


@with_retry(max_retries=CONFIG["RETRY_COUNT"], base_delay=1.0)
def fetch_challan_detail(
    session: requests.Session,
    summary: dict,
    tan:     str,
) -> dict:
    """
    Full detail for a single challan (copychallan endpoint).
    """
    url = API_BASE + "/paymentapi/auth/challan/copychallan"
    crn = summary.get("crn") or summary.get("cin", "")[:14]
    
    payload = {
        "header": {"formName": None},
        "formData": {
            "crn":              crn,
            "pan":              tan,
            "itnsNum":          summary.get("itnsNum") or "281",
            "assessmentYear":   summary.get("assessmentYear") or summary.get("assmentYear") or "",
            "majorHead":        summary.get("majorHead") or "",
            "minorHead":        summary.get("minorHead") or "",
            "majorSlNum":       summary.get("majorSlNum") or summary.get("majorSlNo") or "",
            "minorSlNum":       summary.get("minorSlNum") or summary.get("minorSlNo") or "",
            "totalAmt":         int(float(summary.get("totalAmt") or 0)),
            "tileId":           summary.get("tileId") or "",

            "actType":          summary.get("actType") or "O",
            "loggedInUserID":   tan,
            "loggedInUserType": "TDS",
        },
    }


    return _post(session, url, payload, timeout=CONFIG["TIMEOUT"])




def extend_session(session: requests.Session, tan: str) -> bool:
    """
    Ping extendSession to keep the backend alive.
    Not retried — if it fails, we accept the failure gracefully.
    """
    try:
        url = API_BASE + "/loginapi/auth/extendSession"
        r   = session.post(url, json={"userId": tan}, timeout=15)
        if r.status_code == 200:
            log.info("extendSession OK")
            return True
        log.warning("extendSession HTTP %s", r.status_code)
    except Exception as exc:
        log.warning("extendSession error: %s", exc)
    return False

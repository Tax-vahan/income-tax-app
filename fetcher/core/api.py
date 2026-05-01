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

from ..utils.config import BASE, API_BASE, CONFIG
from .retry import with_retry

log = logging.getLogger("TDS")


# ── Internal POST helper ───────────────────────────────────────────────────────

def _post(
    session: requests.Session,
    url:     str,
    payload: dict,
    timeout: int  = 30,
    headers: dict = None,
) -> dict:
    resp = session.post(url, json=payload, timeout=timeout, headers=headers)
    resp.raise_for_status()
    return resp.json()


# ── Assessment-year derivation ─────────────────────────────────────────────────

def _ay_from_crn(crn: str) -> str:
    """
    Derive ITD assessment year (e.g. '2026-27') from the CRN date prefix YYMMDD.
    Returns '' if the CRN is too short or malformed.
    """
    crn = str(crn or "").strip()
    if len(crn) < 4:
        return ""
    try:
        yy, mm = int(crn[0:2]), int(crn[2:4])
        if not (1 <= mm <= 12):
            return ""
        year     = 2000 + yy
        fy_start = year if mm >= 4 else year - 1
        return f"{fy_start + 1}-{str(fy_start + 2)[-2:]}"
    except (ValueError, TypeError):
        return ""


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


# ITD internal lookup tables — these map portal constants needed by copychallan
_MAJOR_HEAD = {"O": "0021", "C": "0020"}          # actType  → majorHead
_MAJOR_SL   = {"0021": "2", "0020": "1"}           # majorHead → majorSlNum
_MINOR_SL   = {                                    # (majorHead, minorHead) → minorSlNum
    ("0021", "200"): "13", ("0021", "400"): "14",
    ("0020", "200"): "13", ("0020", "400"): "14",
}


@with_retry(max_retries=CONFIG["RETRY_COUNT"], base_delay=1.0)
def fetch_challan_detail(
    session: requests.Session,
    summary: dict,
    tan:     str,
) -> dict:
    """
    Full detail for a single challan (copychallan endpoint).
    """
    url      = API_BASE + "/paymentapi/auth/challan/copychallan"
    crn      = summary.get("crn") or summary.get("cin", "")[:14]
    act_type = summary.get("actType") or "O"
    major_hd = summary.get("majorHead") or _MAJOR_HEAD.get(act_type, "0021")
    minor_hd = summary.get("minorHead") or "200"
    major_sl = (summary.get("majorSlNum") or summary.get("majorSlNo")
                or _MAJOR_SL.get(major_hd, "2"))
    minor_sl = (summary.get("minorSlNum") or summary.get("minorSlNo")
                or _MINOR_SL.get((major_hd, minor_hd), "13"))

    ay = (summary.get("assessmentYear") or summary.get("assmentYear")
          or _ay_from_crn(crn))

    payload = {
        "header": {"formName": None},
        "formData": {
            "crn":            crn,
            "cin":            summary.get("cin") or "",
            "pan":            tan,
            "assessmentYear": ay,
            "majorHead":      major_hd,
            "minorHead":      minor_hd,
            "majorSlNum":     major_sl,
            "minorSlNum":     minor_sl,
            "tileId":         summary.get("tileId") or "12",
            "actType":        act_type,
            "loggedInUserID": tan,
        },
    }

    # Portal validates Referer/Origin; missing them can cause PMT9001.
    extra_headers = {
        "Referer": BASE + "/iec/foservices/#/e-pay-tax",
        "Origin":  BASE,
    }

    log.info("copychallan payload for CRN=%s: %s", crn, payload)
    result = _post(session, url, payload,
                   timeout=CONFIG["TIMEOUT"], headers=extra_headers)
    log.info("copychallan response for CRN=%s: successFlag=%s messages=%s",
             crn, result.get("successFlag"), result.get("messages"))
    return result


@with_retry(max_retries=CONFIG["RETRY_COUNT"], base_delay=1.0)
def fetch_entity_profile(
    session: requests.Session,
    tan:     str,
) -> dict:
    """
    Fetch the deductor's entity master / profile.
    Endpoint: /servicesapi/auth/saveEntity
    Response: flat dict with orgName, pan, tan, address, contact details, timestamps, etc.
    """
    url     = API_BASE + "/servicesapi/auth/saveEntity"
    payload = {
        "header":   {"formName": None},
        "formData": {"pan": tan, "loggedInUserID": tan},
    }
    log.info("Fetching entity profile for TAN=%s", tan)
    result = _post(session, url, payload, timeout=CONFIG["TIMEOUT"])
    log.info(
        "Entity profile: orgName=%s  pan=%s",
        result.get("orgName"), result.get("pan"),
    )
    return result


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

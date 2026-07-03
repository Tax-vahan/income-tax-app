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

from ..utils.config import BASE, API_BASE, CONFIG, act_type_for_date
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
            "actType":        act_type_for_date(from_date),
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
    Full detail for a single challan via the paymentdetails endpoint.

    Payload confirmed from real browser traffic:
      formName = PO-03-PYMNT, cin + entityNum only.
    """
    # Prefer 'cin' as required by paymentdetails; fallback to CRN-based hybrid if needed
    cin = (summary.get("cin") or summary.get("crn") or "").strip()

    url = API_BASE + "/paymentapi/auth/challan/paymentdetails"
    payload = {
        "header": {"formName": "PO-03-PYMNT"},
        "formData": {
            "cin":       cin,
            "entityNum": tan,
        },
    }

    log.info("[API] Sending paymentdetails  CIN=%s", cin)
    log.debug("[API] Payload: %s", payload)

    try:
        resp = session.post(url, json=payload, timeout=CONFIG["TIMEOUT"])
        log.info("[API] Status Code: %s  CIN=%s", resp.status_code, cin)
        resp.raise_for_status()
        result = resp.json()
    except requests.exceptions.RequestException as exc:
        log.error("[API] Network error  CIN=%s: %s", cin, exc)
        raise

    log.info(
        "[API] successFlag=%s  CIN=%s  messages=%s",
        result.get("successFlag"), cin, result.get("messages"),
    )
    if not result.get("successFlag"):
        log.error(
            "[ERROR] paymentdetails failed for CIN=%s\n"
            "  Payload : %s\n"
            "  Response: %s",
            cin, payload, result,
        )
    return result


@with_retry(max_retries=CONFIG["RETRY_COUNT"], base_delay=1.0)
def fetch_entity_profile(
    session: requests.Session,
    tan:     str,
) -> dict:
    """
    Fetch the deductor's entity master / profile.
    Endpoint: /servicesapi/auth/saveEntity
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


@with_retry(max_retries=CONFIG["RETRY_COUNT"], base_delay=1.0)
def fetch_view_filed_forms(
    session: requests.Session,
    tan:     str,
    form_type_cd: str,
) -> dict:
    """
    Fetch filed forms for a specific form type.
    Endpoint: /servicesapi/auth/saveEntity
    """
    url     = API_BASE + "/servicesapi/auth/saveEntity"
    payload = {
        "serviceName": "viewFiledForms",
        "entityNum": tan,
        "formTypeCd": form_type_cd,
        "currentPage": "0",
        "pageSize": "100",
        "filterParameterDetails": []
    }
    log.info("Fetching filed forms for TAN=%s Form=%s", tan, form_type_cd)
    result = _post(session, url, payload, timeout=CONFIG["TIMEOUT"])
    log.info("Fetched %s forms", result.get("responseCount", 0))
    return result


def extend_session(session: requests.Session, tan: str) -> bool:
    """Ping extendSession to keep the backend alive."""
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

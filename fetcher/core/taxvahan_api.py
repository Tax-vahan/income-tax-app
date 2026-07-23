"""
Client for the main TaxVahan backend's own API (api.taxvahan.com) — used only
to fetch manually entered challans for the /tds/api/v1/challan/verify endpoint.

Not the ITD/TRACES portal — see core/api.py for that.

Auth: token-forwarding, not a static service key. The caller of
/tds/api/v1/challan/verify supplies whatever Authorization header value
already works for them against api.taxvahan.com (e.g. "Bearer <jwt>"), and we
forward it verbatim — we never store or assume its format.
"""

import logging

import requests

from ..utils.config import TAXVAHAN_API_BASE
from .retry import with_retry

log = logging.getLogger("TDS")


@with_retry(max_retries=3, base_delay=1.0)
def _fetch_page(
    deductor_id: str,
    financial_year: str,
    quarter: str,
    category_id: int,
    page_number: int,
    page_size: int,
    timeout: int,
    auth_token: str,
) -> dict:
    url = TAXVAHAN_API_BASE.rstrip("/") + "/api/challan/fetch"
    payload = {
        "deductorId":    deductor_id,
        "financialYear": financial_year,
        "quarter":       quarter,
        "categoryId":    category_id,
        "pageNumber":    page_number,
        "pageSize":      page_size,
    }
    headers = {"Authorization": auth_token}
    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_manual_challans(
    deductor_id: str,
    financial_year: str,
    quarter: str,
    category_id: int,
    auth_token: str,
    page_size: int = 50,
    timeout: int = 30,
) -> list[dict]:
    """
    Page through POST /api/challan/fetch and return the full, unfiltered
    challanList for this deductor/FY/quarter/category. Filtering (SectionCode,
    BookEntry) happens separately in
    challan_verification.filter_eligible_manual_challans().

    auth_token is forwarded verbatim as the Authorization header — the caller
    owns its format.
    """
    all_challans: list = []
    page = 1
    while True:
        data = _fetch_page(
            deductor_id, financial_year, quarter, category_id, page, page_size, timeout, auth_token,
        )
        items = data.get("challanList", [])
        total = data.get("totalRows", len(items))
        if not items:
            break
        all_challans.extend(items)
        log.info(
            "TaxVahan challan/fetch page %d: %d / %d challans",
            page, len(all_challans), total,
        )
        if len(all_challans) >= total:
            break
        page += 1
    return all_challans

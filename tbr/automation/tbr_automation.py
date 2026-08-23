"""
Transaction Based Report (TBR) automation for the new TRACES portal
(traces-app.tdscpc.gov.in — CPCTDS 2.0).

Same pattern as pan_verification/automation/challan_bin_automation.py:
authenticate once via TracesLoginService, then call the JSON APIs directly
with the Bearer access_token from session_data, using the Playwright
browser context's request client for GETs and a real page's window.fetch()
for POSTs (confirmed live: POST/write endpoints on this API gateway 404
when sent through Playwright's context.request but succeed through an
actual browser page — see challan_bin_automation.py's _page_post_json
docstring for the full explanation).

Endpoints below were captured live from the portal on 2026-08-23 by
recording real browser network traffic (Downloads > Transaction Based
Report screen). The report-generation endpoint that follows a successful
validate-tan (isValid=true) was NOT observed live — this TAN had no
non-resident-without-PAN payments for the tested quarter, so validate-tan
correctly returned isValid=false and the "Initiate Request" button never
actually fired a generate call. `generate_report()` below is a best-effort
implementation following the same URL/payload conventions as validate-tan
and must be verified against a real isValid=true response before relying
on it in production.
"""
import json

from pan_verification.automation.browser_manager import BrowserManager
from pan_verification.utils.logger import get_logger
from pan_verification.utils.errors import PortalTimeoutError, NavigationFailureError

logger = get_logger(__name__)

API_BASE = "https://traces-app.tdscpc.gov.in/transactionbasedreportservice"

_COMMON_HEADERS_EXTRA = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://traces.tdscpc.gov.in",
    "Referer": "https://traces.tdscpc.gov.in/",
}


def _auth_headers(session_data: dict) -> dict:
    access_token = session_data.get("access_token")
    if not access_token:
        raise NavigationFailureError("No access token found in session data for TBR API call.")
    return {"Authorization": f"Bearer {access_token}", **_COMMON_HEADERS_EXTRA}


def _json_post_headers(session_data: dict) -> dict:
    return {**_auth_headers(session_data), "Content-Type": "application/json; charset=utf-8"}


async def _page_post_json(context, url: str, headers: dict, payload: dict) -> tuple[int, str]:
    """POST via a real page's window.fetch() — see challan_bin_automation.py for why."""
    page = None
    try:
        page = await context.new_page()
    except Exception as e:
        logger.error(f"Failed to create new page: {e}")
        raise PortalTimeoutError(f"Browser context error: {str(e)}")

    try:
        try:
            await page.goto("https://traces.tdscpc.gov.in/", wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.error(f"Failed to navigate to TRACES portal: {e}")
            raise PortalTimeoutError(f"Portal navigation timeout: {str(e)}")

        try:
            result = await page.evaluate(
                """
                async ({ url, headers, body }) => {
                    const resp = await fetch(url, {
                        method: "POST",
                        headers,
                        body,
                        credentials: "include",
                    });
                    const text = await resp.text();
                    return { status: resp.status, text };
                }
                """,
                {"url": url, "headers": headers, "body": json.dumps(payload)},
            )
            return result["status"], result["text"]
        except Exception as e:
            logger.error(f"Failed to execute fetch in page context: {e}")
            raise PortalTimeoutError(f"Request execution failed: {str(e)}")
    finally:
        if page:
            try:
                await page.close()
            except Exception as e:
                logger.warning(f"Error closing page: {e}")


class TBRAutomation:

    async def _get(self, session_data: dict, url: str, params: dict | None = None):
        context = await BrowserManager.get_context()
        try:
            response = await context.request.get(
                url, headers=_auth_headers(session_data), params=params or {},
            )
            if response.status != 200:
                body = await response.text()
                logger.error(f"TBR GET {url} failed with status {response.status}: {body}")
                raise PortalTimeoutError(f"TBR request failed: HTTP {response.status}")
            return await response.json()
        except (PortalTimeoutError, NavigationFailureError):
            raise
        except Exception as e:
            logger.error(f"Error in TBR GET {url}: {str(e)}")
            raise PortalTimeoutError(f"TBR request failed: {str(e)}")

    async def get_financial_years(self, session_data: dict, it_act_flag: bool = False) -> list[dict]:
        """
        GET .../tds/transanctionBasedReport/restapi/getFinYear?itActFlag= -> [{code, description}]

        `itActFlag` is required by the portal (confirmed live: 400 "Required
        parameter 'itActFlag' is missing" when omitted) — presumably
        selecting between Income-tax Act 1961 vs the new 2025 Act, mirroring
        tdscertificatesservice's getFormType/getFinYear. The correct
        true/false meaning is unconfirmed; False is a best-effort default.
        """
        url = f"{API_BASE}/tds/transanctionBasedReport/restapi/getFinYear"
        data = await self._get(session_data, url, {"itActFlag": str(it_act_flag).lower()})
        return data if isinstance(data, list) else data.get("finYear", [])

    async def get_quarters(self, session_data: dict, financial_year: str, it_act_flag: bool = False) -> list[dict]:
        """
        GET .../tds/transanctionBasedReport/restapi/getQuarter?financialYear=YYYY&itActFlag=
        -> [{code, description}]

        itActFlag added defensively (not confirmed required for this
        specific endpoint, but getFinYear needed it despite looking
        parameter-free in the original capture) — harmless if TRACES ignores
        an extra query param.
        """
        url = f"{API_BASE}/tds/transanctionBasedReport/restapi/getQuarter"
        data = await self._get(session_data, url, {
            "financialYear": financial_year,
            "itActFlag": str(it_act_flag).lower(),
        })
        return data if isinstance(data, list) else data.get("quarter", [])

    async def get_ready_download_requests(self, session_data: dict) -> dict:
        """GET .../tds/transactionBasedReport/restapi/ready-download-requests"""
        url = f"{API_BASE}/tds/transactionBasedReport/restapi/ready-download-requests"
        return await self._get(session_data, url)

    async def get_request_history(self, session_data: dict) -> dict:
        """GET .../tds/transactionBasedReport/restapi/request-history"""
        url = f"{API_BASE}/tds/transactionBasedReport/restapi/request-history"
        return await self._get(session_data, url)

    async def validate_tan(self, session_data: dict, tan: str, fin_year: str, quarter_code: str) -> dict:
        """
        POST /api/tbr/validate-tan  body: {tan, finYear, quarter}

        `quarter_code` is the numeric TRACES quarter code (e.g. "3" for Q1 —
        matches the `code` field from get_quarters()), NOT the "Q1" label.

        Confirmed live response shape:
          {"isValid": false, "message": "...", "tan": "...",
           "finYear": "2026", "quarter": 3, "processingStatus": ""}
        A false isValid with a "No statement is available..." message is a
        normal empty-result response, not a transport error.
        """
        context = await BrowserManager.get_context()
        if not context:
            logger.error("BrowserManager context is None")
            raise PortalTimeoutError("Browser context unavailable. System may be initializing.")

        url = f"{API_BASE}/api/tbr/validate-tan"
        payload = {"tan": tan, "finYear": str(fin_year), "quarter": str(quarter_code)}
        try:
            status, raw_text = await _page_post_json(context, url, _json_post_headers(session_data), payload)
            try:
                body = json.loads(raw_text) if raw_text else {}
            except json.JSONDecodeError:
                logger.error(f"validate-tan returned non-JSON body, status {status}: {raw_text[:500]!r}")
                raise PortalTimeoutError(f"TBR validate-tan failed: HTTP {status} with non-JSON body")
            # Both 200 (isValid=true) and 404 (isValid=false, "no statement
            # available") are legitimate business responses — return as-is.
            if body:
                return body
            raise PortalTimeoutError(f"TBR validate-tan failed: HTTP {status} with empty body")
        except (PortalTimeoutError, NavigationFailureError):
            raise
        except Exception as e:
            logger.error(f"Error validating TAN for TBR: {str(e)}")
            raise PortalTimeoutError(f"TBR validate-tan failed: {str(e)}")

    async def generate_report(self, session_data: dict, tan: str, fin_year: str, quarter_code: str) -> dict:
        """
        UNCONFIRMED — best-effort. Not observed on a live isValid=true
        response; verify the URL/payload shape once a TAN with eligible
        (non-resident, no-PAN) transactions is available, using the same
        network-capture approach as validate_tan().

        Best guess, following the validate-tan URL convention:
        POST /api/tbr/generate  body: {tan, finYear, quarter}
        """
        context = await BrowserManager.get_context()
        if not context:
            logger.error("BrowserManager context is None")
            raise PortalTimeoutError("Browser context unavailable. System may be initializing.")

        url = f"{API_BASE}/api/tbr/generate"
        payload = {"tan": tan, "finYear": str(fin_year), "quarter": str(quarter_code)}
        try:
            status, raw_text = await _page_post_json(context, url, _json_post_headers(session_data), payload)
            if status not in (200, 201, 202):
                logger.error(f"TBR generate failed with status {status}: {raw_text[:500]!r}")
                raise PortalTimeoutError(f"TBR generate failed: HTTP {status}")
            return json.loads(raw_text) if raw_text else {}
        except (PortalTimeoutError, NavigationFailureError):
            raise
        except Exception as e:
            logger.error(f"Error generating TBR: {str(e)}")
            raise PortalTimeoutError(f"TBR generate failed: {str(e)}")

"""
Challan/BIN Status automation for the TRACES Deductor Dashboard.

Same pattern as pan_bulk_automation.py: authenticate once via the OTP/login
flow (see login_otp_automation.py), then call the traces-app.tdscpc.gov.in
JSON APIs directly with the Bearer access_token from session_data, using the
Playwright browser context's request client so cookies/TLS fingerprint match
an already-authenticated browser session.
"""
import json

from pan_verification.automation.browser_manager import BrowserManager
from pan_verification.utils.logger import get_logger
from pan_verification.utils.errors import PortalTimeoutError, NavigationFailureError

logger = get_logger(__name__)

API_BASE = "https://traces-app.tdscpc.gov.in/oltaschallancorrection"

_COMMON_HEADERS_EXTRA = {
    "Accept": "*/*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://traces.tdscpc.gov.in",
    "Referer": "https://traces.tdscpc.gov.in/",
}


def _auth_headers(session_data: dict) -> dict:
    access_token = session_data.get("access_token")
    if not access_token:
        raise NavigationFailureError("No access token found in session data for Challan/BIN API call.")
    return {"Authorization": f"Bearer {access_token}", **_COMMON_HEADERS_EXTRA}


def _json_post_headers(session_data: dict) -> dict:
    # Playwright's request.post(data=<dict>) does not reliably serialize as
    # application/json across bindings/versions — confirmed live: the portal's
    # router returned 404 "API not found for this HTTP method" when the dict
    # was sent without an explicit JSON content-type. Callers must pass a
    # json.dumps() string as `data` alongside these headers.
    return {**_auth_headers(session_data), "Content-Type": "application/json; charset=utf-8"}


async def _page_post_json(context, url: str, headers: dict, payload: dict) -> tuple[int, str]:
    """
    POST via a real page's native window.fetch(), not Playwright's standalone
    context.request client.

    Confirmed live: GET calls succeed through context.request, but every POST
    to this API (with an identical URL/headers/body to a working browser
    capture, preflight included) comes back 404 "API not found for this HTTP
    method". context.request is Playwright's own Node-side HTTP client — it
    shares cookies with the browser context but does not go through Chromium's
    actual network stack, so its TLS/HTTP2 fingerprint differs from genuine
    browser traffic. That fits a gateway/WAF that fingerprints write endpoints
    more strictly than read-only ones (GET dropdowns) rather than any header,
    body, or CORS-preflight difference — all of which were ruled out.

    Runs the fetch inside an actual page on the traces.tdscpc.gov.in origin so
    it carries the real browser's network identity. Returns (status, raw_text).
    """
    page = await context.new_page()
    try:
        await page.goto("https://traces.tdscpc.gov.in/", wait_until="domcontentloaded")
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
    finally:
        await page.close()


class ChallanBinAutomation:

    async def _get_dropdown(self, session_data: dict, path: str, params: dict | None = None) -> dict:
        context = await BrowserManager.get_context()
        try:
            url = f"{API_BASE}/api/dropdown/{path}"
            response = await context.request.get(
                url, headers=_auth_headers(session_data), params=params or {},
            )
            if response.status != 200:
                body = await response.text()
                logger.error(f"Dropdown '{path}' failed with status {response.status}: {body}")
                raise PortalTimeoutError(f"Dropdown '{path}' failed: HTTP {response.status}")
            return await response.json()
        except (PortalTimeoutError, NavigationFailureError):
            raise
        except Exception as e:
            logger.error(f"Error fetching dropdown '{path}': {str(e)}")
            raise PortalTimeoutError(f"Dropdown '{path}' failed: {str(e)}")

    async def get_form_types(self, session_data: dict) -> list[dict]:
        data = await self._get_dropdown(session_data, "deductorForm-types")
        return data.get("formType", [])

    async def get_financial_years(self, session_data: dict) -> list[dict]:
        data = await self._get_dropdown(session_data, "financial-year")
        # This endpoint returns a bare list, not {"...": [...]}.
        return data if isinstance(data, list) else data.get("financialYear", [])

    async def get_quarters(self, session_data: dict, financial_year: str) -> list[dict]:
        data = await self._get_dropdown(session_data, "quarters", {"fyTy": financial_year})
        return data.get("quarter", [])

    async def get_consumption_statuses(self, session_data: dict) -> list[str]:
        data = await self._get_dropdown(session_data, "deductorConsumptionStatus")
        return data.get("ConsumptionStatus", [])

    async def get_challan_statuses(self, session_data: dict) -> list[dict]:
        data = await self._get_dropdown(session_data, "challan-status")
        return data.get("challanStatus", [])

    async def get_export_formats(self, session_data: dict) -> list[dict]:
        data = await self._get_dropdown(session_data, "format")
        return data.get("format", [])

    async def search_period_of_payment(
        self,
        session_data: dict,
        tan: str,
        from_date: str,
        to_date: str,
        match_status: str,
        consumption_status: list[str] | None,
        page: int,
        size: int,
    ) -> dict:
        """
        POST /deductor/bin/periodOfpayment — the "Period of payment" Challan/BIN
        status search. Confirmed via live browser capture: a 400 with
        {"message": "No Challans Found"} is the portal's normal "no results"
        response, not a transport error — callers should treat it as an empty
        result set rather than raising.
        """
        context = await BrowserManager.get_context()
        try:
            url = f"{API_BASE}/deductor/bin/periodOfpayment?page={page}&size={size}"
            payload = {
                "tan":               tan,
                "fromDate":          from_date,
                "toDate":            to_date,
                "matchStatus":       match_status,
                "consumptionStatus": consumption_status,
            }
            logger.info(f"periodOfpayment POST (via page fetch) -> {url}")
            status, raw_text = await _page_post_json(context, url, _json_post_headers(session_data), payload)
            logger.info(f"periodOfpayment status = {status}")
            try:
                body = json.loads(raw_text) if raw_text else None
            except json.JSONDecodeError:
                logger.error(f"periodOfpayment returned non-JSON body, status {status}: {raw_text[:1000]!r}")
                raise PortalTimeoutError(
                    f"Challan/BIN search failed: HTTP {status} with non-JSON body "
                    f"(first 200 chars: {raw_text[:200]!r})"
                )
            if status == 200:
                return body
            if status == 400 and (body or {}).get("message") == "No Challans Found":
                return body
            logger.error(f"periodOfpayment search failed with status {status}: {body}")
            raise PortalTimeoutError(f"Challan/BIN search failed: HTTP {status}: {body}")
        except (PortalTimeoutError, NavigationFailureError):
            raise
        except Exception as e:
            logger.error(f"Error searching Challan/BIN status: {str(e)}")
            raise PortalTimeoutError(f"Challan/BIN search failed: {str(e)}")

    async def export_download(
        self,
        session_data: dict,
        format_code: str,
        tan: str,
        from_date: str,
        to_date: str,
        match_status: str,
        consumption_status: list[str] | None,
    ) -> tuple[bytes, str]:
        """
        POST /api/deductor-bin-export/download — generates the same result
        set as search_period_of_payment() but as a downloadable file.

        Response is {"format": ..., "base64Data": "...", "fileName": "..."}
        (confirmed via live browser capture). Returns (raw_file_bytes, filename).
        """
        context = await BrowserManager.get_context()
        try:
            url = f"{API_BASE}/api/deductor-bin-export/download"
            payload = {
                "format":            format_code,
                "tan":               tan,
                "fromDate":          from_date,
                "toDate":            to_date,
                "matchStatus":       match_status,
                "consumptionStatus": consumption_status,
            }
            status, raw_text = await _page_post_json(context, url, _json_post_headers(session_data), payload)
            if status != 200:
                logger.error(f"deductor-bin-export/download failed with status {status}: {raw_text[:1000]!r}")
                raise PortalTimeoutError(f"Challan/BIN export failed: HTTP {status}")

            body = json.loads(raw_text)
            base64_data = body.get("base64Data")
            filename = body.get("fileName") or f"DeductorBinDetails.{format_code.lower()}"
            if not base64_data:
                raise PortalTimeoutError("Challan/BIN export response had no base64Data.")

            import base64
            return base64.b64decode(base64_data), filename
        except (PortalTimeoutError, NavigationFailureError):
            raise
        except Exception as e:
            logger.error(f"Error exporting Challan/BIN status: {str(e)}")
            raise PortalTimeoutError(f"Challan/BIN export failed: {str(e)}")

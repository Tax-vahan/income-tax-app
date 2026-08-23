"""
TDS/TCS Certificate download automation for the new TRACES portal
(traces-app.tdscpc.gov.in — CPCTDS 2.0, "tdscertificatesservice").

Same pattern as pan_verification/automation/challan_bin_automation.py:
GETs go through the Playwright context's request client, POSTs go through
a real page's window.fetch() (write endpoints on this API gateway 404
when sent through Playwright's context.request — see
challan_bin_automation.py's _page_post_json docstring for the full
explanation of why).

Endpoints and the getFormType/getFinYear/getQuarter/getActiveRequests/
getDownloadStatus/request-history response shapes below were captured
live from the portal on 2026-08-23 (Downloads > Download TDS/TCS
Certificates screen), including a real `initiate` POST (downloadType=14,
"All PANs" option). The file-download endpoint itself was NOT observed
live — the only two requests on this TAN were either still "Processing"
(status=1, no download yet possible) or already downloaded in a prior
session (no re-download link exposed in the UI). `download()` below is a
best-effort implementation and must be verified against a real
status="Available"/ready request before relying on it in production.
"""
import base64
import json

from pan_verification.automation.browser_manager import BrowserManager
from pan_verification.utils.logger import get_logger
from pan_verification.utils.errors import PortalTimeoutError, NavigationFailureError

logger = get_logger(__name__)

API_BASE = "https://traces-app.tdscpc.gov.in/tdscertificatesservice"

_COMMON_HEADERS_EXTRA = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://traces.tdscpc.gov.in",
    "Referer": "https://traces.tdscpc.gov.in/",
}

# Confirmed live: "All PANs" option -> downloadType 14. The "PAN" (up to 10)
# and "Bulk PAN Upload" (up to 500) options were not exercised live, so their
# downloadType codes are unknown — ALL_PANS is the only value used below.
DOWNLOAD_TYPE_ALL_PANS = 14


def _auth_headers(session_data: dict) -> dict:
    access_token = session_data.get("access_token")
    if not access_token:
        raise NavigationFailureError("No access token found in session data for TDS/TCS API call.")
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


class TDSTCSAutomation:

    async def _get(self, session_data: dict, url: str, params: dict | None = None):
        context = await BrowserManager.get_context()
        try:
            response = await context.request.get(
                url, headers=_auth_headers(session_data), params=params or {},
            )
            if response.status != 200:
                body = await response.text()
                logger.error(f"TDS/TCS GET {url} failed with status {response.status}: {body}")
                raise PortalTimeoutError(f"TDS/TCS request failed: HTTP {response.status}")
            return await response.json()
        except (PortalTimeoutError, NavigationFailureError):
            raise
        except Exception as e:
            logger.error(f"Error in TDS/TCS GET {url}: {str(e)}")
            raise PortalTimeoutError(f"TDS/TCS request failed: {str(e)}")

    async def get_form_types(self, session_data: dict, it_act_flag: bool = True) -> list[dict]:
        """
        GET tdscerts/restapi/getFormType?itActFlag= -> [{code, description}] e.g. 130/131/133

        itActFlag=true (Income-tax Act 2025) is the default here — 130/131/133
        are 2025-Act certificate codes, and itActFlag=false (Act 1961) was
        confirmed live 2026-08-23 to make TRACES itself throw an unhandled
        500 ("Internal Server Error") for this combination, not a clean 4xx.
        """
        url = f"{API_BASE}/tdscerts/restapi/getFormType"
        data = await self._get(session_data, url, {"itActFlag": str(it_act_flag).lower()})
        return data if isinstance(data, list) else data.get("formType", [])

    async def get_financial_years(self, session_data: dict, it_act_flag: bool = False) -> list[dict]:
        """GET tdscerts/restapi/getFinYear?itActFlag= -> [{code, description}]"""
        url = f"{API_BASE}/tdscerts/restapi/getFinYear"
        data = await self._get(session_data, url, {"itActFlag": str(it_act_flag).lower()})
        return data if isinstance(data, list) else data.get("finYear", [])

    async def get_quarters(self, session_data: dict, financial_year: str) -> list[dict]:
        """GET tdscerts/restapi/getQuarter?financialYear= -> [{code, description, displayName}]"""
        url = f"{API_BASE}/tdscerts/restapi/getQuarter"
        data = await self._get(session_data, url, {"financialYear": financial_year})
        return data if isinstance(data, list) else data.get("quarter", [])

    async def get_active_requests(
        self, session_data: dict, page: int = 0, size: int = 10, fetch_all: bool = False
    ) -> dict:
        """
        GET getActiveRequests?userId=&fetchAll=&page=&size=
        -> {content:[{requestId, financialYear, formType, quarter, requestDate,
                      status, expiryDate, remarks}], currentPage, totalPages,
            totalElements, pageSize, hasNext, hasPrevious, first, last}
        """
        url = f"{API_BASE}/getActiveRequests"
        return await self._get(session_data, url, {
            "userId": session_data["tan"],
            "fetchAll": str(fetch_all).lower(),
            "page": page,
            "size": size,
        })

    async def get_request_history(self, session_data: dict) -> dict:
        """GET request-history?userId="""
        url = f"{API_BASE}/request-history"
        return await self._get(session_data, url, {"userId": session_data["tan"]})

    async def get_download_status(self, session_data: dict, request_id: str) -> dict:
        """
        GET getDownloadStatus?requestId= -> {"status": <int>}
        Confirmed live: status=1 while the request is still processing.
        The "ready" status code was not observed live (both test requests
        were still processing or already consumed) — treat any value other
        than 1 as "not still-pending" and let the caller decide readiness
        from getActiveRequests()/request-history()'s text status field
        instead, which is more reliable until this is confirmed.
        """
        url = f"{API_BASE}/getDownloadStatus"
        return await self._get(session_data, url, {"requestId": request_id})

    async def initiate(
        self,
        session_data: dict,
        financial_year: str,
        quarter: str,
        form_type: str,
    ) -> dict:
        """
        POST /initiate  body: {userId, tan, financialYear, quarter, formType, downloadType}
        Confirmed live shape (downloadType=14, "All PANs"). Only the
        "All PANs" flow is implemented — "PAN" (specific PANs) and "Bulk
        PAN Upload" need their downloadType codes and extra payload fields
        (panList / uploaded file reference) captured from a live session
        before they can be added here.
        """
        context = await BrowserManager.get_context()
        if not context:
            logger.error("BrowserManager context is None")
            raise PortalTimeoutError("Browser context unavailable. System may be initializing.")

        tan = session_data.get("tan")
        if not tan:
            logger.error(f"Session data missing TAN: {list(session_data.keys())}")
            raise NavigationFailureError("Session missing TAN information")

        url = f"{API_BASE}/initiate"
        payload = {
            "userId": tan,
            "tan": tan,
            "financialYear": financial_year,
            "quarter": quarter,
            "formType": form_type,
            "downloadType": DOWNLOAD_TYPE_ALL_PANS,
        }
        try:
            status, raw_text = await _page_post_json(context, url, _json_post_headers(session_data), payload)
            try:
                body = json.loads(raw_text) if raw_text else {}
            except json.JSONDecodeError:
                body = {"raw": raw_text}
            if status not in (200, 201, 202):
                logger.error(f"TDS/TCS initiate failed with status {status}: {raw_text[:500]!r}")
                raise PortalTimeoutError(f"TDS/TCS initiate failed: HTTP {status}: {body}")
            return body
        except (PortalTimeoutError, NavigationFailureError):
            raise
        except Exception as e:
            logger.error(f"Error initiating TDS/TCS certificate download: {str(e)}", exc_info=True)
            raise PortalTimeoutError(f"TDS/TCS initiate failed: {str(e)}")

    async def download(self, session_data: dict, request_id: str) -> tuple[bytes, str]:
        """
        CONFIRMED live 2026-08-23: GET /downloadFile?requestId= (NOT /download)
        streams the file as raw octet-stream bytes directly — response is
        Content-Type: application/octet-stream with the real filename in
        Content-Disposition, e.g.
          attachment; filename="PTLxxxxx1E_FORM131_2026_Q1_250992273_1.zip"
        The JSON+base64Data branch below is kept only as a defensive fallback
        in case TRACES changes response shape; the raw-bytes branch is the
        one that actually fires in production.
        """
        context = await BrowserManager.get_context()
        url = f"{API_BASE}/downloadFile"
        try:
            response = await context.request.get(
                url, headers=_auth_headers(session_data), params={"requestId": request_id},
            )
            if response.status != 200:
                body = await response.text()
                logger.error(f"TDS/TCS download failed with status {response.status}: {body}")
                raise PortalTimeoutError(f"TDS/TCS download failed: HTTP {response.status}")

            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                data = await response.json()
                base64_data = data.get("base64Data")
                filename = data.get("fileName") or f"TDSTCS_{request_id}.zip"
                if not base64_data:
                    raise PortalTimeoutError("TDS/TCS download response had no base64Data.")
                return base64.b64decode(base64_data), filename

            # Fallback: server streamed raw bytes directly.
            filename = f"TDSTCS_{request_id}.zip"
            cd = response.headers.get("content-disposition", "")
            if "filename=" in cd:
                filename = cd.split("filename=")[-1].strip('"\'')
            return await response.body(), filename
        except (PortalTimeoutError, NavigationFailureError):
            raise
        except Exception as e:
            logger.error(f"Error downloading TDS/TCS certificate: {str(e)}")
            raise PortalTimeoutError(f"TDS/TCS download failed: {str(e)}")

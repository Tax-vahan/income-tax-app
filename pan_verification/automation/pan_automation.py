import asyncio
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from pan_verification.automation.browser_manager import BrowserManager
from pan_verification.core.page_pool import get_page_pool
from pan_verification.selectors.registry import SelectorRegistry
from pan_verification.utils.config import settings
from pan_verification.utils.logger import get_logger
from pan_verification.utils.errors import (
    PortalTimeoutError,
    InvalidPanError,
    NavigationFailureError,
    SelectorNotConfiguredError
)

logger = get_logger(__name__)


class PanAutomation:

    @retry(
        stop=stop_after_attempt(settings.RETRY_ATTEMPTS),
        wait=wait_fixed(settings.RETRY_WAIT_TIME),
        retry=retry_if_exception_type((PortalTimeoutError, NavigationFailureError))
    )
    async def verify_pan(self, session_id: str, session_data: dict, pan: str, form_type: str) -> dict:
        logger.info(f"Starting PAN verification for {pan}")
        context = await BrowserManager.get_context()
        page_pool = get_page_pool()

        # Get a page from the pool instead of creating a new one
        page = await page_pool.get_page(session_id, context)

        try:
            # 1. Restore session cookies if not already present
            if "cookies" in session_data:
                logger.debug("Restoring session cookies...")
                await context.add_cookies(session_data["cookies"])

            # 2. Navigate to TRACES and access PAN Verification
            logger.debug("Navigating to TRACES portal...")
            await page.goto(settings.TRACES_URL, wait_until="domcontentloaded", timeout=settings.TIMEOUT)

            logger.debug("Clicking to PAN Verification section...")
            try:
                await page.click(SelectorRegistry.MENU_STATEMENTS_PAYMENTS())
                await page.click(SelectorRegistry.SUBMENU_PAN_VERIFICATION())
            except SelectorNotConfiguredError as e:
                logger.error(f"Selector not configured: {e}")
                raise

            await page.wait_for_load_state("networkidle", timeout=15000)

            # 3. Fill PAN and Form Type
            logger.debug(f"Filling PAN and form type...")
            await page.fill(SelectorRegistry.PAN_VERIFY_PAN_INPUT(), pan)
            await page.select_option(SelectorRegistry.PAN_VERIFY_FORM_TYPE_SELECT(), form_type)

            # 4. Submit
            logger.debug("Submitting PAN verification form...")
            await page.click(SelectorRegistry.PAN_VERIFY_SUBMIT_BUTTON())

            # 5. Parse Result
            logger.debug("Waiting for verification result...")
            try:
                await page.wait_for_selector(SelectorRegistry.PAN_RESULT_HOLDER_NAME(), timeout=10000)
            except PlaywrightTimeoutError:
                if await page.locator(SelectorRegistry.PAN_RESULT_ERROR()).is_visible():
                    logger.warning(f"Invalid PAN: {pan}")
                    raise InvalidPanError(f"Invalid PAN: {pan}")
                logger.error(f"Timed out waiting for PAN verification result")
                raise PortalTimeoutError("Timed out waiting for PAN verification result")

            holder_name = await page.locator(SelectorRegistry.PAN_RESULT_HOLDER_NAME()).text_content()
            status = await page.locator(SelectorRegistry.PAN_RESULT_STATUS()).text_content()

            logger.info(f"PAN verification successful for {pan}")

            return {
                "pan": pan,
                "holder_name": holder_name.strip() if holder_name else "",
                "status": status.strip() if status else "",
                "is_valid": True
            }

        except InvalidPanError:
            logger.warning(f"Invalid PAN encountered: {pan}")
            await self._capture_error_state(page, f"pan_invalid_{pan}")
            raise
        except PlaywrightTimeoutError as e:
            logger.error(f"PAN verification timed out: {str(e)}")
            await self._capture_error_state(page, f"pan_timeout_{pan}")
            raise PortalTimeoutError(f"PAN verification timed out: {str(e)}")
        except SelectorNotConfiguredError as e:
            logger.error(f"Selector not configured during PAN verification: {e}")
            await self._capture_error_state(page, f"pan_selector_error_{pan}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during PAN verification for {pan}: {str(e)}")
            await self._capture_error_state(page, f"pan_error_{pan}")
            raise
        finally:
            # Release the page back to pool instead of closing
            await page_pool.release_page(session_id)

    async def _capture_error_state(self, page: Page, prefix: str):
        if settings.DEBUG_MODE:
            try:
                logger.debug(f"Capturing error state: {prefix}")
                await page.screenshot(path=f"{prefix}_screenshot.png")
                html = await page.content()
                with open(f"{prefix}_source.html", "w") as f:
                    f.write(html)
            except Exception as e:
                logger.warning(f"Failed to capture error state: {e}")

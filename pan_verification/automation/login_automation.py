from pathlib import Path
import asyncio
import base64
import time
from io import BytesIO
from typing import Tuple
from PIL import Image
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from pan_verification.automation.browser_manager import BrowserManager
from pan_verification.utils.config import settings
from pan_verification.utils.logger import get_logger
from pan_verification.utils.errors import (
    PortalTimeoutError,
    InvalidCredentialsError,
    NavigationFailureError,
    CaptchaFailureError
)

logger = get_logger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://traces.tdscpc.gov.in",
    "Referer": "https://traces.tdscpc.gov.in/"
}

class LoginAutomation:
    async def get_captcha_api(self) -> Tuple[str, str, str]:
        """
        Uses API to fetch the captcha, bypassing the Flutter UI canvas.
        Returns (captcha_id, captcha_base64, captcha_text).
        User must provide captcha text manually.
        """
        context = await BrowserManager.get_context()

        try:
            logger.info("Fetching captcha via API...")
            api_url = "https://traces-app.tdscpc.gov.in/loginservice/api/auth/generateCaptcha"

            response = await context.request.get(api_url, headers=HEADERS)

            if not response.ok:
                raise PortalTimeoutError(f"Failed to fetch captcha API: {response.status}")

            data = await response.json()
            captcha_id = data.get("id")
            captcha_base64 = data.get("image")

            if not captcha_id or not captcha_base64:
                raise PortalTimeoutError("Captcha API response missing id or image")

            # Save captcha image for reference
            import os
            import time as _time

            image_bytes = base64.b64decode(captcha_base64)
            captcha_dir = str(Path(__file__).resolve().parent.parent.parent / "captchas")
            os.makedirs(captcha_dir, exist_ok=True)
            captcha_filename = f"{captcha_dir}/captcha_{int(_time.time())}.png"
            with open(captcha_filename, "wb") as f:
                f.write(image_bytes)
            logger.info(f"Captcha image saved to: {captcha_filename}")

            logger.info("Successfully fetched captcha via API")
            return captcha_id, captcha_base64, ""

        except Exception as e:
            logger.error(f"Unexpected error during API captcha fetch: {str(e)}")
            raise PortalTimeoutError(f"Failed to load API captcha: {str(e)}")

    async def submit_login_api(self, tan: str, password: str, captcha: str, captcha_id: str) -> dict:
        """
        Uses API to submit credentials, retrieves tokens, and injects them into a Playwright page.

        Args:
            tan: Tax Account Number
            password: Login password
            captcha: Captcha text (manually entered by user or auto-extracted via OCR)
            captcha_id: Captcha ID from get_captcha_api()
        """
        context = await BrowserManager.get_context()

        try:
            logger.info(f"Attempting API login for TAN: {tan[:2]}****{tan[-2:]}")

            if not captcha or not captcha.strip():
                raise InvalidCredentialsError("Captcha text is required. Please provide the captcha code you see in the image.")

            api_url = "https://traces-app.tdscpc.gov.in/loginservice/api/auth/login"

            payload = {
                "userId": tan,
                "userType": "Deductor",
                "subUserPanId": "",
                "password": password,
                "captcha": captcha.strip(),
                "captchaId": captcha_id
            }

            logger.debug(f"Login payload: userId={tan}, userType=Deductor, captchaId={captcha_id[:8]}..., captcha_length={len(captcha)}")

            # 1. Authenticate via API
            response = await context.request.post(api_url, data=payload, headers=HEADERS)

            # Capture response body for debugging
            response_body = None
            try:
                response_body = await response.json()
            except:
                response_body = await response.text()

            logger.debug(f"API Login Response Status: {response.status}, Body: {response_body}")

            data = response_body if isinstance(response_body, dict) else {}
            
            # TRACES returns 403 with JSON for invalid captcha
            if isinstance(response_body, dict) and "isAuthenticated" in response_body:
                if not response_body.get("isAuthenticated"):
                    msg = response_body.get("message") or response_body.get("errorCode") or "Unknown error"
                    logger.warning(f"Authentication failed: {msg}")
                    if "verification code" in msg.lower() or "captcha" in msg.lower() or "cpt" in msg.lower():
                        raise CaptchaFailureError(f"Invalid Captcha: {msg}")
                    else:
                        raise InvalidCredentialsError(f"Invalid Credentials: {msg}")
            
            if not response.ok:
                logger.error(f"API Login HTTP Error: {response.status}")
                logger.error(f"Error Response: {response_body}")
                raise PortalTimeoutError(f"API Login failed: {response.status} - {response_body}")

            if not data.get("isAuthenticated"):
                raise InvalidCredentialsError("Authentication failed but no error message provided")

            access_token = data["authTokenDto"]["accessToken"]
            refresh_token = data["authTokenDto"]["refreshToken"]
            logger.info("API Authentication Successful. Tokens retrieved.")
            
            # 2. Bridge Session to Playwright Context
            page = await context.new_page()
            try:
                # Go to legacy domain to set cookies correctly
                legacy_url = "https://traces61.tdscpc.gov.in"
                await page.goto(legacy_url, wait_until="domcontentloaded")
                
                # Add auth cookies
                await context.add_cookies([
                    {"name": "authToken", "value": access_token, "domain": ".tdscpc.gov.in", "path": "/"},
                    {"name": "refreshToken", "value": refresh_token, "domain": ".tdscpc.gov.in", "path": "/"}
                ])
                
                logger.info("Session bridged successfully into Playwright.")
                
                # Capture final session state
                cookies = await context.cookies()
                storage_state = await context.storage_state()
                
                return {
                    "cookies": cookies,
                    "storage_state": storage_state,
                    "access_token": access_token,
                    "refresh_token": refresh_token
                }
            finally:
                # We don't need the page to stay open in the pool for login since session state is saved!
                await page.close()

        except (InvalidCredentialsError, CaptchaFailureError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error during API login: {str(e)}")
            raise PortalTimeoutError(f"API login failed: {str(e)}")

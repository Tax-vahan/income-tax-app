"""
Alternative OTP-based login for TRACES portal.
Use this if password-based API login fails with 403 error.
"""
import asyncio
import base64
from io import BytesIO
from typing import Tuple
from PIL import Image
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from pan_verification.automation.browser_manager import BrowserManager
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


class LoginOTPAutomation:
    """Alternative OTP-based login for TRACES portal."""

    async def send_otp(self, tan: str) -> Tuple[str, str]:
        """
        Request OTP from TRACES.
        Returns (otp_id, delivery_method)

        Tries multiple API endpoints as the exact endpoint may vary.
        """
        context = await BrowserManager.get_context()

        endpoints = [
            "https://traces-app.tdscpc.gov.in/loginservice/api/auth/sendOtp",
            "https://traces.tdscpc.gov.in/api/auth/sendOtp",
            "https://traces-app.tdscpc.gov.in/api/otp/send",
        ]

        for api_url in endpoints:
            try:
                logger.info(f"Attempting OTP send to: {api_url}")
                response = await context.request.post(
                    api_url,
                    data={"userId": tan},
                    headers=HEADERS
                )

                if response.ok:
                    data = await response.json()
                    otp_id = data.get("otpId") or data.get("transactionId") or data.get("id")
                    delivery_method = data.get("deliveryMethod") or data.get("channel") or "SMS"

                    if otp_id:
                        logger.info(f"OTP sent successfully via {delivery_method}")
                        return otp_id, delivery_method
                    else:
                        logger.warning(f"OTP response missing ID: {data}")
                else:
                    logger.debug(f"Endpoint {api_url} returned {response.status}")

            except Exception as e:
                logger.debug(f"OTP endpoint {api_url} failed: {str(e)}")
                continue

        raise PortalTimeoutError("Could not send OTP - all endpoints failed")

    async def verify_otp(self, tan: str, otp: str, otp_id: str) -> Tuple[str, str]:
        """
        Verify OTP and get authentication tokens.
        Returns (access_token, refresh_token)

        Tries multiple API endpoints as the exact endpoint may vary.
        """
        context = await BrowserManager.get_context()

        endpoints = [
            "https://traces-app.tdscpc.gov.in/loginservice/api/auth/verifyOtp",
            "https://traces.tdscpc.gov.in/api/auth/verifyOtp",
            "https://traces-app.tdscpc.gov.in/api/otp/verify",
        ]

        payloads = [
            {
                "userId": tan,
                "otp": otp,
                "otpId": otp_id,
                "userType": "Deductor"
            },
            {
                "userId": tan,
                "otp": otp,
                "transactionId": otp_id,
            },
            {
                "tan": tan,
                "otp": otp,
                "otpId": otp_id,
            },
        ]

        for api_url in endpoints:
            for payload in payloads:
                try:
                    logger.info(f"Attempting OTP verify to: {api_url}")
                    response = await context.request.post(
                        api_url,
                        data=payload,
                        headers=HEADERS
                    )

                    response_body = None
                    try:
                        response_body = await response.json()
                    except:
                        response_body = await response.text()

                    if response.ok and response_body.get("isAuthenticated"):
                        access_token = response_body["authTokenDto"]["accessToken"]
                        refresh_token = response_body["authTokenDto"]["refreshToken"]
                        logger.info("OTP verification successful. Tokens retrieved.")
                        return access_token, refresh_token
                    else:
                        logger.debug(f"OTP verify failed with {api_url}: {response_body}")

                except Exception as e:
                    logger.debug(f"OTP verify endpoint {api_url} failed: {str(e)}")
                    continue

        raise PortalTimeoutError("Could not verify OTP - all endpoints failed")

    async def complete_otp_login(self, tan: str, otp_user_input: str) -> dict:
        """
        Complete login using OTP.

        Workflow:
        1. Send OTP request
        2. Wait for user to provide OTP (passed as parameter)
        3. Verify OTP and get tokens
        4. Inject tokens into Playwright context

        Args:
            tan: Tax Account Number
            otp_user_input: OTP provided by user (6 digits typically)
        """
        context = await BrowserManager.get_context()

        try:
            logger.info(f"Starting OTP login for TAN: {tan[:2]}****{tan[-2:]}")

            # Step 1: Send OTP
            otp_id, delivery_method = await self.send_otp(tan)
            logger.info(f"OTP sent via {delivery_method}. Waiting for user input...")

            # Step 2: User provides OTP (already done in this implementation)
            if not otp_user_input or len(otp_user_input) < 4:
                raise InvalidCredentialsError("Invalid OTP provided")

            # Step 3: Verify OTP
            access_token, refresh_token = await self.verify_otp(tan, otp_user_input, otp_id)

            # Step 4: Bridge session to Playwright
            page = await context.new_page()
            try:
                legacy_url = "https://traces61.tdscpc.gov.in"
                await page.goto(legacy_url, wait_until="domcontentloaded")

                await context.add_cookies([
                    {"name": "authToken", "value": access_token, "domain": ".tdscpc.gov.in", "path": "/"},
                    {"name": "refreshToken", "value": refresh_token, "domain": ".tdscpc.gov.in", "path": "/"}
                ])

                logger.info("OTP session bridged successfully into Playwright.")

                cookies = await context.cookies()
                storage_state = await context.storage_state()

                return {
                    "cookies": cookies,
                    "storage_state": storage_state,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "login_method": "OTP"
                }
            finally:
                await page.close()

        except Exception as e:
            logger.error(f"OTP login failed: {str(e)}")
            raise PortalTimeoutError(f"OTP login failed: {str(e)}")

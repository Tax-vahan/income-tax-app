import base64
from http.client import HTTPException
import uuid

from pan_verification.automation.login_automation import LoginAutomation
from pan_verification.automation.login_otp_automation import LoginOTPAutomation
from pan_verification.core import get_session_manager, session_manager
from pan_verification.utils.logger import get_logger
from pan_verification.utils.errors import (
    SessionExpiredError,
    PortalTimeoutError,
    InvalidCredentialsError,
)

logger = get_logger(__name__)


class TracesLoginService:
    def __init__(self):
        self.automation = LoginAutomation()
        self.otp_automation = LoginOTPAutomation()

    async def init_login(self, tan: str, password: str) -> dict:
        """
        Initializes the login flow by fetching the captcha via API.
        Returns captcha image. User must provide captcha text manually.
        """
        logger.info("Initializing login flow via API...")
        session_manager = await get_session_manager()

        # Get captcha via API
        captcha_id, captcha_base64, _ = await self.automation.get_captcha_api()

        # Create session
        session_id = await session_manager.create_session()

        # Save credentials and captcha data
        await session_manager.update_session(
            session_id,
            {
                "captcha_id": captcha_id,
                "tan": tan,
                "password": password,
                "captcha_base64": captcha_base64,
            },
        )

        logger.info(f"Login session created: {session_id}")

        return {
            "session_id": session_id,
            "captcha_id": captcha_id,
            "captcha_base64": captcha_base64,
            "message": "Decode captcha_base64 image and provide the text in /login/complete",
        }

    async def login(
        self, session_id: str, tan: str, password: str, captcha: str
    ) -> dict:
        """
        Completes the API login flow.
        Captcha can be: OCR-extracted (user confirmed) or manually entered by user.
        """
        logger.info(f"Attempting API login for session: {session_id}")
        session_manager = await get_session_manager()

        # Get session
        session = await session_manager.get_session(session_id)
        if session is None:
            logger.warning(f"Session not found: {session_id}")
            raise SessionExpiredError(
                "Session not found or expired. Please request a new captcha."
            )

        try:
            # Extract captcha_id and credentials from session
            captcha_id = session.data.get("captcha_id")
            session_tan = session.data.get("tan")
            session_password = session.data.get("password")

            if not captcha_id:
                raise SessionExpiredError(
                    "captcha_id not found in session. Please request a new captcha."
                )

            logger.info(f"User provided captcha: {captcha}")

            # Validate captcha is provided
            if not captcha or not captcha.strip():
                raise InvalidCredentialsError(
                    "Captcha text is required. Please enter the code from the image."
                )

            # Use provided credentials if different from session (shouldn't happen)
            final_tan = tan if tan else session_tan
            final_password = password if password else session_password

            # Submit login via API
            session_data = await self.automation.submit_login_api(
                final_tan, final_password, captcha.strip(), captcha_id
            )

            # Update session with login data
            await session_manager.update_session(session_id, session_data)

            logger.info(f"API Login successful for session: {session_id}")

            return {"success": True, "session_id": session_id, "logged_in": True}

        except (InvalidCredentialsError, SessionExpiredError):
            raise
        except Exception as e:
            logger.error(f"Login failed for session {session_id}: {str(e)}")
            # On any error, clean up
            await session_manager.delete_session(session_id)
            raise

    async def auto_login(self, tan: str, password: str) -> str:
        """
        Fully automated login using OCR for captcha solving.
        Returns the session_id if successful.
        """
        import ddddocr
        import base64
        import asyncio
        from pan_verification.utils.errors import CaptchaFailureError

        ocr = ddddocr.DdddOcr(show_ad=False)
        max_attempts = 3

        for attempt in range(max_attempts):
            try:
                logger.info(
                    f"Auto-login attempt {attempt + 1}/{max_attempts} for TAN: {tan}"
                )
                # 1. Init login to get captcha
                init_result = await self.init_login(tan, password)
                session_id = init_result["session_id"]
                captcha_base64 = init_result["captcha_base64"]

                # 2. Solve captcha with OCR
                image_bytes = base64.b64decode(captcha_base64)
                captcha_text = ocr.classification(image_bytes)
                logger.info(f"OCR extracted captcha: {captcha_text}")

                # 3. Complete login
                result = await self.login(session_id, tan, password, captcha_text)
                if result.get("success"):
                    logger.info(f"Auto-login successful on attempt {attempt + 1}")
                    return session_id

            except CaptchaFailureError as e:
                logger.warning(f"Captcha failure on attempt {attempt + 1}: {str(e)}")
                # The login() method cleans up the session on error, so we will get a new one next loop
                if attempt == max_attempts - 1:
                    raise
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Auto-login failed on attempt {attempt + 1}: {str(e)}")
                if attempt == max_attempts - 1:
                    raise
                await asyncio.sleep(1)

        raise InvalidCredentialsError("Failed to auto-login after multiple attempts.")

    async def restore_session(self, session_id: str) -> dict:
        """
        Validate and return session data.
        """
        logger.debug(f"Restoring session: {session_id}")
        session_manager = await get_session_manager()

        session = await session_manager.get_session(session_id)
        if session is None:
            logger.warning(f"Failed to restore session: {session_id}")
            raise SessionExpiredError("Session not found or expired")

        if not session.is_active():
            logger.warning(f"Session not active/logged in: {session_id}")
            raise SessionExpiredError("Session not logged in")

        logger.debug(f"Session restored successfully: {session_id}")
        return session.data


async def refresh_captcha(self, session_id: str):
    try:
        session = await session_manager.get_session(session_id)

        if session is None:
            logger.warning(f"Failed to restore session: {session_id}")
            raise SessionExpiredError("Session not found or expired")

        page = session["page"]

        # Refresh captcha
        await page.click("#captchaRefresh")

        # Wait for new captcha to load
        await page.wait_for_timeout(1500)

        captcha_element = await page.query_selector("#captchaImage")

        if captcha_element is None:
            raise Exception("Captcha image not found")

        captcha_bytes = await captcha_element.screenshot()
        captcha_base64 = base64.b64encode(captcha_bytes).decode("utf-8")

        return {
            "session_id": session_id,
            "captcha_id": str(uuid.uuid4()),
            "captcha_base64": captcha_base64,
            "message": "New captcha generated",
        }

    except SessionExpiredError:
        raise

    except Exception as e:
        logger.error(
            f"Error refreshing captcha for session {session_id}: {str(e)}",
            exc_info=True,
        )
        raise


async def validate_session(self, session_id: str) -> bool:
    """
    Validate if session exists and is active.
    """
    logger.debug(f"Validating session: {session_id}")
    session_manager = await get_session_manager()
    is_valid = await session_manager.validate_session(session_id)

    if is_valid:
        logger.debug(f"Session is valid and active: {session_id}")
    else:
        logger.debug(f"Session is invalid or expired: {session_id}")

    return is_valid


async def login_with_fallback(
    self, session_id: str, tan: str, password: str, captcha: str
) -> dict:
    """
    Try password-based login first. If it fails with 403 (permission/auth error),
    fall back to OTP-based login.

    This handles the case where the API login endpoint might be restricted.
    """
    logger.info(f"Attempting login with fallback for TAN: {tan[:2]}****{tan[-2:]}")
    session_manager = await get_session_manager()

    # Get session
    session = await session_manager.get_session(session_id)
    if session is None:
        raise SessionExpiredError("Session not found or expired")

    try:
        captcha_id = session.data.get("captcha_id")
        if not captcha_id:
            raise SessionExpiredError("captcha_id not found in session")

        # Try password-based login first
        logger.info("Attempting password-based API login...")
        try:
            session_data = await self.automation.submit_login_api(
                tan, password, captcha, captcha_id
            )
            logger.info("Password-based login successful")
            await session_manager.update_session(session_id, session_data)
            return {
                "success": True,
                "session_id": session_id,
                "logged_in": True,
                "method": "password",
            }
        except PortalTimeoutError as e:
            error_msg = str(e).lower()
            if "403" in error_msg or "forbidden" in error_msg:
                logger.warning(
                    "Password login failed with 403 - falling back to OTP..."
                )
            else:
                raise

        # Fall back to OTP login if password fails
        logger.info("Attempting OTP-based login as fallback...")
        logger.warning(
            "OTP login requires user to manually provide OTP code via SMS/Email"
        )
        logger.warning(
            "This is a fallback method when API password authentication is restricted"
        )

        raise InvalidCredentialsError(
            "Password API login failed with 403. OTP login available - user must provide OTP code manually."
        )

    except Exception as e:
        logger.error(f"Login with fallback failed: {str(e)}")
        await session_manager.delete_session(session_id)
        raise

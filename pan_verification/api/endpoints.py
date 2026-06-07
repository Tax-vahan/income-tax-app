import base64
from fastapi import APIRouter, Depends, Path, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse
import io

from pan_verification.models.schemas import (
    CaptchaResponse,
    LoginInitRequest,
    LoginCompleteRequest,
    LoginRequest,
    LoginResponse,
    PanVerifyRequest,
    PanVerifyResponse,
    SessionStatusResponse,
    BulkPanUploadResponse,
    BulkPanStatusResponse,
    ErrorResponse,
    ResendCaptchaRequest,
)
from pan_verification.services.login_service import TracesLoginService
from pan_verification.services.pan_service import PanVerificationService
from pan_verification.core.session_manager import get_session_manager
from pan_verification.utils.logger import get_logger
from pan_verification.utils.validators import (
    validate_pan,
    validate_tan,
    validate_password,
    validate_captcha,
    validate_form_type,
    validate_session_id,
)

logger = get_logger(__name__)
router = APIRouter()


def get_login_service() -> TracesLoginService:
    return TracesLoginService()


def get_pan_service() -> PanVerificationService:
    return PanVerificationService()


# ===========================================================================
# AUTHENTICATION
# ===========================================================================


@router.post("/login/init", response_model=CaptchaResponse, tags=["Authentication"])
async def login_init(
    request: LoginInitRequest,
    login_service: TracesLoginService = Depends(get_login_service),
):
    """
    Get captcha image for login (Step 1 of 2-step flow).

    **Flow:**
    1. POST /login/init with TAN and password → returns session_id + captcha image
    2. Decode captcha_base64 image and read the text manually
    3. POST /login/complete with session_id, captcha text, and credentials

    **Returns:**
    - session_id: Session ID for /login/complete
    - captcha_base64: PNG image (base64) - decode and display to user
    - captcha_id: Captcha ID (used internally)
    - message: Instructions
    """
    logger.info(f"POST /login/init - Initializing login for TAN: {request.tan[:2]}****")

    try:
        is_valid, msg = validate_tan(request.tan)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid TAN: {msg}")

        is_valid, msg = validate_password(request.password)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid password: {msg}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail="Validation error")

    try:
        result = await login_service.init_login(
            tan=request.tan, password=request.password
        )
        logger.info(f"Captcha retrieved for session {result['session_id']}")
        return result
    except Exception as e:
        logger.error(f"Error initializing login: {str(e)}", exc_info=True)
        raise


@router.post("/login/complete", response_model=LoginResponse, tags=["Authentication"])
async def login_complete(
    request: LoginCompleteRequest,
    login_service: TracesLoginService = Depends(get_login_service),
):
    """
    Complete login with captcha (Step 2 of 2-step flow).

    **Flow:**
    1. POST /login/init → receive captcha image
    2. User manually reads image and provides captcha text
    3. POST /login/complete with session_id, credentials, and captcha text

    **Parameters:**
    - session_id: From /login/init response
    - tan, password: Tax Account Number and password
    - captcha: Captcha text (user must read from image and type manually)

    **On success:** Returns session_id for PAN verification endpoints
    **On failure:** If captcha is wrong, call /login/init again for new captcha
    """
    logger.info(
        f"POST /login/complete - Completing login for session {request.session_id}"
    )

    try:
        is_valid, msg = validate_session_id(request.session_id)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid session ID: {msg}")

        is_valid, msg = validate_tan(request.tan)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid TAN: {msg}")

        is_valid, msg = validate_password(request.password)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid password: {msg}")

        is_valid, msg = validate_captcha(request.captcha)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid captcha: {msg}")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail="Validation error")

    try:
        result = await login_service.login(
            session_id=request.session_id,
            tan=request.tan,
            password=request.password,
            captcha=request.captcha,
        )
        logger.info(f"Login successful for session {request.session_id}")
        return result
    except Exception as e:
        logger.error(f"Error during login completion: {str(e)}", exc_info=True)
        raise


@router.post(
    "/captcha/resend", response_model=CaptchaResponse, tags=["Authentication"]
)
async def resend_captcha(
    request: ResendCaptchaRequest,
    login_service: TracesLoginService = Depends(get_login_service),
):
    """
    Resend captcha for an existing login session.

    Returns:
    - session_id
    - captcha_base64
    - captcha_id
    - message
    """

    logger.info(
        f"POST /captcha/resend - Resending captcha for session {request.session_id}"
    )

    try:
        is_valid, msg = validate_session_id(request.session_id)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid session ID: {msg}")

        result = await login_service.resend_captcha(session_id=request.session_id)

        logger.info(f"Captcha resent for session {request.session_id}")

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resending captcha: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to resend captcha")


# ===========================================================================
# PAN VERIFICATION
# ===========================================================================


@router.post("/pan/verify", response_model=PanVerifyResponse, tags=["PAN Verification"])
async def verify_pan(
    request: PanVerifyRequest,
    pan_service: PanVerificationService = Depends(get_pan_service),
):
    """
    Verify a single PAN number on TRACES portal.
    """
    logger.info(
        f"POST /pan/verify - Verifying PAN {request.pan} for session {request.session_id}"
    )

    try:
        is_valid, msg = validate_session_id(request.session_id)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid session ID: {msg}")

        is_valid, msg = validate_pan(request.pan)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid PAN: {msg}")

        is_valid, msg = validate_form_type(request.form_type)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid form type: {msg}")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail="Validation error")

    try:
        result = await pan_service.verify_pan(
            session_id=request.session_id, pan=request.pan, form_type=request.form_type
        )
        return result
    except Exception as e:
        logger.error(f"Error verifying PAN: {str(e)}", exc_info=True)
        raise


@router.post(
    "/pan/bulk-upload",
    response_model=BulkPanUploadResponse,
    tags=["Bulk PAN Verification"],
)
async def bulk_upload_pan(
    session_id: str = Form(..., description="Active session ID from /login/complete"),
    file: UploadFile = File(..., description="CSV file containing PAN numbers"),
    pan_service: PanVerificationService = Depends(get_pan_service),
):
    """
    Upload a CSV file of PAN numbers to TRACES for bulk verification.

    The CSV should contain PAN numbers (TRACES-accepted format).
    Returns a token_number to track the job.

    Steps:
    1. Upload CSV here → get token_number
    2. Poll GET /pan/status/{token_number} until is_ready=true
    3. Call GET /pan/download/{token_number}?session_id=... to download the result
    """
    logger.info(f"POST /pan/bulk-upload - Session: {session_id}, File: {file.filename}")

    try:
        is_valid, msg = validate_session_id(session_id)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid session ID: {msg}")

        if not file.filename or not file.filename.endswith(".csv"):
            raise HTTPException(status_code=400, detail="Only CSV files are accepted")

        csv_bytes = await file.read()
        if len(csv_bytes) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail="File validation error")

    try:
        result = await pan_service.bulk_upload_pan(
            session_id=session_id, csv_bytes=csv_bytes, filename=file.filename
        )
        logger.info(f"Bulk upload successful, token: {result['token_number']}")
        return result
    except Exception as e:
        logger.error(f"Error during bulk PAN upload: {str(e)}", exc_info=True)
        raise


@router.get(
    "/pan/status/{token_number}",
    response_model=BulkPanStatusResponse,
    tags=["Bulk PAN Verification"],
)
async def pan_status(
    token_number: str = Path(..., description="Token number from /pan/bulk-upload"),
    session_id: str = "",
    pan_service: PanVerificationService = Depends(get_pan_service),
):
    """
    Check if the bulk PAN verification file is ready to download.

    Poll this endpoint after /pan/bulk-upload until is_ready=true,
    then call /pan/download/{token_number}.
    """
    logger.info(f"GET /pan/status/{token_number}")

    if not session_id:
        raise HTTPException(
            status_code=400, detail="session_id query parameter is required"
        )

    try:
        result = await pan_service.check_download_status(
            session_id=session_id, token_number=token_number
        )
        return result
    except Exception as e:
        logger.error(f"Error checking PAN status: {str(e)}", exc_info=True)
        raise


@router.get("/pan/download/{token_number}", tags=["Bulk PAN Verification"])
async def pan_download(
    token_number: str = Path(..., description="Token number from /pan/bulk-upload"),
    session_id: str = "",
    pan_service: PanVerificationService = Depends(get_pan_service),
):
    """
    Download the generated bulk PAN verification file from TRACES.

    Call this after /pan/status/{token_number} returns is_ready=true.
    Returns the file as a downloadable attachment.
    """
    logger.info(f"GET /pan/download/{token_number}")

    if not session_id:
        raise HTTPException(
            status_code=400, detail="session_id query parameter is required"
        )

    try:
        file_bytes, filename = await pan_service.download_pan_file(
            session_id=session_id, token_number=token_number
        )
        return StreamingResponse(
            io.BytesIO(file_bytes),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        logger.error(f"Error downloading PAN file: {str(e)}", exc_info=True)
        raise


# ===========================================================================
# FULLY AUTOMATED PAN VERIFICATION (Auto-Login & OCR)
# ===========================================================================


async def _get_auto_session_id(
    tan: str, password: str, login_service: TracesLoginService
) -> str:
    """Helper to get an existing session by TAN or auto-login if needed."""
    session_manager = await get_session_manager()
    session_id = await session_manager.get_session_by_tan(tan)
    if session_id:
        logger.info(f"Reusing existing session {session_id} for TAN {tan}")
        return session_id

    logger.info(f"No active session for TAN {tan}. Triggering auto-login...")
    return await login_service.auto_login(tan, password)


@router.post(
    "/pan/auto/bulk-upload",
    response_model=BulkPanUploadResponse,
    tags=["Fully Automated"],
)
async def auto_bulk_upload_pan(
    tan: str = Form(..., description="Tax Account Number"),
    password: str = Form(..., description="Login Password"),
    file: UploadFile = File(..., description="CSV file containing PAN numbers"),
    login_service: TracesLoginService = Depends(get_login_service),
    pan_service: PanVerificationService = Depends(get_pan_service),
):
    """
    Fully automated bulk PAN upload.
    If no active session exists for the TAN, it automatically solves the captcha and logs in.
    """
    try:
        session_id = await _get_auto_session_id(tan, password, login_service)

        csv_bytes = await file.read()
        result = await pan_service.bulk_upload_pan(
            session_id=session_id, csv_bytes=csv_bytes, filename=file.filename
        )
        return result
    except Exception as e:
        logger.error(f"Error in auto bulk upload: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/pan/auto/status/{token_number}",
    response_model=BulkPanStatusResponse,
    tags=["Fully Automated"],
)
async def auto_pan_status(
    token_number: str = Path(
        ..., description="Token number from /pan/auto/bulk-upload"
    ),
    tan: str = "",
    password: str = "",
    login_service: TracesLoginService = Depends(get_login_service),
    pan_service: PanVerificationService = Depends(get_pan_service),
):
    """Check status automatically."""
    try:
        if not tan or not password:
            raise HTTPException(
                status_code=400, detail="tan and password query parameters are required"
            )

        session_id = await _get_auto_session_id(tan, password, login_service)
        return await pan_service.check_download_status(
            session_id=session_id, token_number=token_number
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pan/auto/download/{token_number}", tags=["Fully Automated"])
async def auto_pan_download(
    token_number: str = Path(
        ..., description="Token number from /pan/auto/bulk-upload"
    ),
    tan: str = "",
    password: str = "",
    login_service: TracesLoginService = Depends(get_login_service),
    pan_service: PanVerificationService = Depends(get_pan_service),
):
    """Download automatically."""
    try:
        if not tan or not password:
            raise HTTPException(
                status_code=400, detail="tan and password query parameters are required"
            )

        session_id = await _get_auto_session_id(tan, password, login_service)
        file_bytes, filename = await pan_service.download_pan_file(
            session_id=session_id, token_number=token_number
        )

        return StreamingResponse(
            io.BytesIO(file_bytes),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# SESSION MANAGEMENT
# ===========================================================================


@router.get(
    "/session/status/{session_id}",
    response_model=SessionStatusResponse,
    tags=["Session Management"],
)
async def get_session_status(
    session_id: str = Path(..., description="The session ID to check"),
    login_service: TracesLoginService = Depends(get_login_service),
):
    """Check if a session is active and valid."""
    logger.debug(f"GET /session/status/{session_id}")

    try:
        is_valid, msg = validate_session_id(session_id)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid session ID: {msg}")
    except HTTPException:
        raise

    try:
        is_active = await login_service.validate_session(session_id)
        return {"active": is_active, "expired": not is_active}
    except Exception as e:
        logger.error(f"Error checking session status: {str(e)}", exc_info=True)
        raise


@router.get("/health", tags=["Monitoring"])
async def health_check():
    """Health check endpoint for monitoring."""
    return {
        "status": "healthy",
        "message": "TRACES PAN Verification Service is running",
    }

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Error message")


class CaptchaResponse(BaseModel):
    session_id: str = Field(..., description="Session ID for this login flow - use in /login/complete")
    captcha_base64: str = Field(..., description="Base64 PNG image of captcha - decode and display to user")
    captcha_id: str = Field(..., description="Captcha ID - send back in /login/complete")
    message: str = Field(..., description="Instructions for user")


class LoginInitRequest(BaseModel):
    tan: str = Field(..., description="Tax Account Number (10 characters)")
    password: str = Field(..., description="TRACES login password")


class LoginCompleteRequest(BaseModel):
    session_id: str = Field(..., description="Session ID from /login/init")
    tan: str = Field(..., description="Tax Account Number (10 characters)")
    password: str = Field(..., description="TRACES login password")
    captcha: str = Field(..., description="Captcha text solution")


class LoginRequest(BaseModel):
    session_id: str = Field(..., description="Session ID from /captcha endpoint")
    tan: str = Field(..., description="Tax Account Number (10 characters)")
    password: str = Field(..., description="TRACES login password")
    captcha: str = Field(..., description="Captcha text solution")
    captcha_id: str = Field(..., description="Unique ID for the captcha solution")


class LoginResponse(BaseModel):
    success: bool = Field(..., description="Whether login was successful")
    session_id: str | None = Field(None, description="Session ID if successful")
    logged_in: bool = Field(..., description="Whether user is logged in")
    
class RefreshCaptchaRequest(BaseModel):
    session_id: str


class PanVerifyRequest(BaseModel):
    session_id: str = Field(..., description="Active session ID from /login")
    pan: str = Field(..., description="PAN number to verify (10 characters, AAAAA9999A format)")
    form_type: str = Field(..., description="ITR Form Type (e.g., 24Q, 24G, 24F)")


class PanVerifyResponse(BaseModel):
    pan: str = Field(..., description="The PAN number that was verified")
    holder_name: str | None = Field(None, description="Name of PAN holder if valid")
    status: str | None = Field(None, description="PAN verification status from TRACES")
    is_valid: bool = Field(..., description="Whether PAN is valid")


class SessionStatusResponse(BaseModel):
    active: bool = Field(..., description="Whether session is active and logged in")
    expired: bool = Field(..., description="Whether session has expired")


class BulkPanUploadResponse(BaseModel):
    success: bool = Field(..., description="Whether the file was uploaded successfully")
    token_number: str = Field(..., description="TRACES request/token number for tracking the job")
    message: str | None = Field(None, description="Additional message from TRACES")


class BulkPanStatusResponse(BaseModel):
    token_number: str = Field(..., description="TRACES token number")
    status: str = Field(..., description="Status of the request (e.g., Submitted, Available)")
    is_ready: bool = Field(..., description="Whether the file is ready to download")

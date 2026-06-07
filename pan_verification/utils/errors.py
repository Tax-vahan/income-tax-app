from fastapi import HTTPException


class AutomationError(Exception):
    """Base class for automation errors."""
    pass


class SelectorNotConfiguredError(AutomationError):
    """Raised when a selector is not properly configured in selectors.json."""
    pass

class PortalTimeoutError(AutomationError):
    """Raised when the TRACES portal times out."""
    pass

class InvalidCredentialsError(AutomationError):
    """Raised for invalid TAN or password."""
    pass

class CaptchaFailureError(AutomationError):
    """Raised when the provided captcha is incorrect."""
    pass

class SessionExpiredError(AutomationError):
    """Raised when the session has expired or is invalid."""
    pass

class InvalidPanError(AutomationError):
    """Raised when PAN format or value is rejected by the portal."""
    pass

class NavigationFailureError(AutomationError):
    """Raised when portal navigation fails unexpectedly."""
    pass

def map_automation_error_to_http(e: Exception) -> HTTPException:
    if isinstance(e, InvalidCredentialsError):
        return HTTPException(status_code=401, detail="Invalid TAN or Password")
    if isinstance(e, CaptchaFailureError):
        return HTTPException(status_code=400, detail="Invalid Captcha")
    if isinstance(e, SessionExpiredError):
        return HTTPException(status_code=401, detail="Session expired or invalid")
    if isinstance(e, InvalidPanError):
        return HTTPException(status_code=400, detail="Invalid PAN")
    if isinstance(e, PortalTimeoutError):
        return HTTPException(status_code=504, detail="TRACES portal timeout")
    if isinstance(e, NavigationFailureError):
        return HTTPException(status_code=502, detail="TRACES portal navigation failure")
    
    # Catch-all for unexpected automation errors
    return HTTPException(status_code=500, detail=f"Internal automation error: {str(e)}")

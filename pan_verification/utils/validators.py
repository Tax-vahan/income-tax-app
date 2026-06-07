import re
from typing import Tuple

def validate_pan(pan: str) -> Tuple[bool, str]:
    """
    Validate PAN format.
    Valid PAN: 10 alphanumeric characters (flexible format)
    Standard: AAAAA9999A (5 letters, 4 digits, 1 letter)
    Also accepts other valid government formats
    """
    if not pan or not isinstance(pan, str):
        return False, "PAN must be a non-empty string"

    pan = pan.strip().upper()

    if len(pan) != 10:
        return False, "PAN must be exactly 10 characters"

    # Accept any 10-character alphanumeric PAN (flexible format)
    if not re.match(r'^[A-Z0-9]{10}$', pan):
        return False, "Invalid PAN format. Expected: 10 alphanumeric characters"

    return True, "Valid PAN"


def validate_tan(tan: str) -> Tuple[bool, str]:
    """
    Validate TAN format.
    Valid TAN: 10 alphanumeric characters (flexible format)
    Examples: AAAAA9999A, PTLA13241E, etc.
    """
    if not tan or not isinstance(tan, str):
        return False, "TAN must be a non-empty string"

    tan = tan.strip().upper()

    if len(tan) != 10:
        return False, "TAN must be exactly 10 characters"

    # Accept any 10-character alphanumeric TAN (flexible format)
    if not re.match(r'^[A-Z0-9]{10}$', tan):
        return False, "Invalid TAN format. Expected: 10 alphanumeric characters (e.g., PTLA13241E)"

    return True, "Valid TAN"


def validate_password(password: str) -> Tuple[bool, str]:
    """
    Validate password format (basic checks).
    """
    if not password or not isinstance(password, str):
        return False, "Password must be a non-empty string"

    if len(password) < 6:
        return False, "Password must be at least 6 characters"

    if len(password) > 100:
        return False, "Password must be less than 100 characters"

    return True, "Valid password"


def validate_captcha(captcha: str) -> Tuple[bool, str]:
    """
    Validate captcha text.
    """
    if not captcha or not isinstance(captcha, str):
        return False, "Captcha must be a non-empty string"

    captcha = captcha.strip()

    if len(captcha) < 3:
        return False, "Captcha too short"

    if len(captcha) > 10:
        return False, "Captcha too long"

    return True, "Valid captcha"


def validate_form_type(form_type: str) -> Tuple[bool, str]:
    """
    Validate form type.
    """
    valid_forms = {
        '24Q', '24G', '24F', '27EQ', '27Q', '27A',
        '26Q', '26QRS', '26AS', 'Others', 'other'
    }

    if not form_type or not isinstance(form_type, str):
        return False, "Form type must be a non-empty string"

    form_type = form_type.strip().upper()

    # Allow custom form types but suggest valid ones
    if form_type not in valid_forms:
        return True, f"Custom form type: {form_type}. Common forms: {', '.join(sorted(valid_forms))}"

    return True, "Valid form type"


def validate_session_id(session_id: str) -> Tuple[bool, str]:
    """
    Validate session ID format (UUID).
    """
    if not session_id or not isinstance(session_id, str):
        return False, "Session ID must be a non-empty string"

    session_id = session_id.strip()

    # Simple UUID format check
    if not re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', session_id, re.I):
        return False, "Invalid session ID format (must be UUID)"

    return True, "Valid session ID"

from pan_verification.utils.selectors import get_selector

class SelectorRegistry:
    """
    Selectors are now managed through selectors.json config file.
    This class provides convenient access to selectors via static properties.
    To configure selectors, edit selectors.json with actual CSS/XPath selectors.
    """

    # Login Page
    @staticmethod
    def LOGIN_TAN_INPUT():
        return get_selector("login.tan_input")

    @staticmethod
    def LOGIN_PASSWORD_INPUT():
        return get_selector("login.password_input")

    @staticmethod
    def LOGIN_CAPTCHA_INPUT():
        return get_selector("login.captcha_input")

    @staticmethod
    def LOGIN_CAPTCHA_IMAGE():
        return get_selector("login.captcha_image")

    @staticmethod
    def LOGIN_SUBMIT_BUTTON():
        return get_selector("login.submit_button")

    @staticmethod
    def LOGIN_ERROR_MESSAGE():
        return get_selector("login.error_message")

    @staticmethod
    def LOGIN_CAPTCHA_ERROR():
        return get_selector("login.captcha_error")

    # Dashboard / Post-Login
    @staticmethod
    def DASHBOARD_INDICATOR():
        return get_selector("dashboard.indicator")

    # Navigation
    @staticmethod
    def MENU_STATEMENTS_PAYMENTS():
        return get_selector("navigation.menu_statements_payments")

    @staticmethod
    def SUBMENU_PAN_VERIFICATION():
        return get_selector("navigation.submenu_pan_verification")

    # PAN Verification Page
    @staticmethod
    def PAN_VERIFY_PAN_INPUT():
        return get_selector("pan_verification.pan_input")

    @staticmethod
    def PAN_VERIFY_FORM_TYPE_SELECT():
        return get_selector("pan_verification.form_type_select")

    @staticmethod
    def PAN_VERIFY_SUBMIT_BUTTON():
        return get_selector("pan_verification.submit_button")

    # PAN Verification Results
    @staticmethod
    def PAN_RESULT_HOLDER_NAME():
        return get_selector("pan_results.holder_name")

    @staticmethod
    def PAN_RESULT_STATUS():
        return get_selector("pan_results.status")

    @staticmethod
    def PAN_RESULT_ERROR():
        return get_selector("pan_results.error")

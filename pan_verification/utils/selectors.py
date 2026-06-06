import json
import os
from typing import Dict, Optional

class SelectorManager:
    _instance: Optional['SelectorManager'] = None
    _selectors: Dict = {}
    _loaded = False

    def __init__(self):
        if not SelectorManager._loaded:
            self.load_selectors()

    @classmethod
    def load_selectors(cls, config_path: Optional[str] = None) -> None:
        """Load selectors from JSON config file."""
        if cls._loaded:
            return

        if config_path is None:
            config_path = os.path.join(os.getcwd(), "selectors.json")

        try:
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    cls._selectors = json.load(f)
                print(f"Selectors loaded from {config_path}")
            else:
                print(f"Warning: selectors.json not found at {config_path}. Using defaults.")
                cls._selectors = cls._get_default_selectors()
        except json.JSONDecodeError as e:
            print(f"Error parsing selectors.json: {e}. Using defaults.")
            cls._selectors = cls._get_default_selectors()

        cls._loaded = True

    @staticmethod
    def _get_default_selectors() -> Dict:
        """Return default selector structure with TODO placeholders."""
        return {
            "login": {
                "tan_input": "TODO: Selector for TAN input",
                "password_input": "TODO: Selector for password input",
                "captcha_input": "TODO: Selector for captcha input",
                "captcha_image": "TODO: Selector for captcha image element",
                "submit_button": "TODO: Selector for submit button",
                "error_message": "TODO: Selector for general login error",
                "captcha_error": "TODO: Selector for captcha error"
            },
            "dashboard": {
                "indicator": "TODO: Selector that confirms logged in (e.g., logout button or profile name)"
            },
            "navigation": {
                "menu_statements_payments": "TODO: Selector for 'Statements / Payments' menu",
                "submenu_pan_verification": "TODO: Selector for 'PAN Verification' sub-menu item"
            },
            "pan_verification": {
                "pan_input": "TODO: Selector for PAN input",
                "form_type_select": "TODO: Selector for Form Type dropdown",
                "submit_button": "TODO: Selector for Submit button"
            },
            "pan_results": {
                "holder_name": "TODO: Selector for PAN holder name result",
                "status": "TODO: Selector for PAN status result",
                "error": "TODO: Selector for invalid PAN error message"
            }
        }

    def get(self, path: str, default: Optional[str] = None) -> str:
        """
        Get selector by dot-notation path.
        Example: get("login.tan_input")
        """
        keys = path.split('.')
        value = self._selectors

        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default or f"ERROR: Selector not found: {path}"

        if value is None:
            return default or f"ERROR: Selector not found: {path}"

        if isinstance(value, str) and value.startswith("TODO"):
            raise SelectorNotConfiguredError(f"Selector not configured: {path} - {value}")

        return value

    def get_all(self) -> Dict:
        """Get all selectors."""
        return self._selectors

    @classmethod
    def get_instance(cls) -> 'SelectorManager':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


class SelectorNotConfiguredError(Exception):
    """Raised when a selector is not properly configured."""
    pass


# Global instance for easy access
_selector_manager = SelectorManager.get_instance()

def get_selector(path: str) -> str:
    """Convenience function to get a selector."""
    return _selector_manager.get(path)

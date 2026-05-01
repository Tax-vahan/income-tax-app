"""
Authentication module — Selenium login + session persistence.

Public API
----------
get_session(cfg) → (session, driver, cdp_capture)
    Main entry point.  Returns a live requests.Session plus optional
    Selenium objects (None when a saved session was successfully reused).

Flow
----
1. Try loading + validating a saved session from disk.
   → If valid: return (session, None, None)  — Selenium never starts.
2. Otherwise: launch headless Chrome, log in, navigate to Payment History
   (which triggers real Angular API calls), wait for CDP header capture,
   build requests.Session, save to disk.
   → Return (session, driver, cdp_capture)
     The caller must call driver.quit() and cdp_capture.stop() when done.
"""

import json
import os
import re
import time
import logging
import subprocess
import pathlib
import requests
from typing import Optional, Tuple

from ..utils.config import BASE, API_BASE, USER_AGENT
from .cdp import CDPCapture

log = logging.getLogger("TDS")

# ── Selenium UI helpers ────────────────────────────────────────────────────────

def _tick_checkbox(driver) -> None:
    """Tick the SAM checkbox using JS — bypasses Angular Material visibility guards."""
    from selenium.webdriver.common.by import By

    cb = None
    for sel in [
        "input[type='checkbox']",
        "input[formcontrolname='isSecureMsg']",
        "mat-checkbox input",
    ]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            cb = els[0]
            break

    if not cb:
        log.warning("SAM checkbox not found — skipping")
        return
    if cb.is_selected():
        return

    # Try clicking the Angular Material label first (preferred path)
    try:
        label = driver.find_element(
            By.CSS_SELECTOR,
            "mat-checkbox label, label[for='isSecureMsg'], .mat-checkbox-label",
        )
        driver.execute_script("arguments[0].click();", label)
        time.sleep(0.3)
        if cb.is_selected():
            return
    except Exception:
        pass

    # Fallback: force-set via JS events
    driver.execute_script("""
        var cb = arguments[0];
        cb.checked = true;
        cb.dispatchEvent(new MouseEvent('click',  {bubbles: true}));
        cb.dispatchEvent(new Event('change', {bubbles: true}));
        cb.dispatchEvent(new Event('input',  {bubbles: true}));
    """, cb)
    time.sleep(0.3)


def _handle_dual_login(driver) -> None:
    """Click 'Login Here' if the Dual Login Detected popup appears."""
    from selenium.webdriver.common.by import By
    try:
        if "Dual Login" not in driver.find_element(By.TAG_NAME, "body").text:
            return
        log.info("Dual login popup detected — clicking 'Login Here' ...")
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if "Login Here" in btn.text:
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(1)

                return
    except Exception:
        pass


# ── Chrome setup ───────────────────────────────────────────────────────────────

def _chrome_major_version(binary: str) -> Optional[int]:
    try:
        # Discard stderr so Chrome's WARNING lines (with their timestamps) don't
        # pollute stdout and fool the version regex.
        out = subprocess.check_output(
            [binary, "--version"], stderr=subprocess.DEVNULL, timeout=5
        ).decode()
        # Match only the version number that follows "Chrome" or "ChromeDriver"
        m = re.search(r"(?:Chrome|ChromeDriver)\s+(\d+)\.\d+", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _start_chrome(proxy: str = ""):
    """
    Locate a compatible Chrome binary + ChromeDriver and return a WebDriver.
    Returns None if Chrome cannot be started.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
    except ImportError:
        log.error("selenium not installed — run: pip install selenium")
        return None

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(f"--user-agent={USER_AGENT}")
    # Performance logging streams CDP events through driver.get_log()
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    if proxy:
        opts.add_argument(f"--proxy-server={proxy}")
        log.info("Using proxy: %s", proxy)

    # Honour Docker env-var override first (set in docker-compose.yml)
    _env_chrome = os.environ.get("CHROME_BIN", "")
    _CHROME_CANDIDATES = list(filter(None, [
        _env_chrome,                           # Docker / CI override
        "/usr/bin/chromium",                   # Debian package (Docker image)
        "/usr/bin/chromium-browser",           # Ubuntu alias
        "/opt/google/chrome/chrome",           # Google Chrome deb
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/snap/bin/chromium",
    ]))
    chrome_bin = chrome_ver = None
    for cp in _CHROME_CANDIDATES:
        if not os.path.exists(cp):
            continue
        try:
            resolved = str(pathlib.Path(cp).resolve())
            if os.path.isfile(resolved):
                cp = resolved
        except Exception:
            pass
        chrome_bin = cp
        opts.binary_location = cp
        chrome_ver = _chrome_major_version(cp)
        log.info("Chrome binary: %s (v%s)", cp, chrome_ver)
        break

    _env_driver = os.environ.get("CHROMEDRIVER_BIN", "")
    _home = os.path.expanduser("~")
    _DRIVER_CANDIDATES = list(filter(None, [
        _env_driver,                                     # Docker / CI override
        "/usr/bin/chromedriver",                         # Debian chromium-driver
        f"{_home}/.local/bin/chromedriver",
        "/usr/local/bin/chromedriver",
        "/opt/google/chrome/chromedriver",
        "/usr/lib/chromium-browser/chromedriver",
        "/usr/lib/chromium/chromedriver",
        "/snap/bin/chromedriver",
    ]))
    for dp in _DRIVER_CANDIDATES:
        if not os.path.exists(dp):
            continue
        dv = _chrome_major_version(dp)
        if chrome_ver and dv and dv != chrome_ver:
            log.warning("Skip %s — driver v%s ≠ chrome v%s", dp, dv, chrome_ver)
            continue
        try:
            from selenium.webdriver.chrome.service import Service
            driver = webdriver.Chrome(service=Service(dp), options=opts)
            log.info("ChromeDriver: %s (v%s)", dp, dv)
            return driver
        except Exception as exc:
            log.debug("ChromeDriver %s failed: %s", dp, exc)

    try:
        driver = webdriver.Chrome(options=opts)
        log.info("ChromeDriver: (resolved from PATH)")
        return driver
    except Exception as exc:
        tip = (
            f"\n  Fix: download matching ChromeDriver for Chrome {chrome_ver}"
            f" and place it at ~/.local/bin/chromedriver"
        ) if chrome_ver else ""
        log.error("Cannot start Chrome: %s%s", exc, tip)
        return None


# ── Login flow ─────────────────────────────────────────────────────────────────

def login(tan: str, password: str, proxy: str = "") -> Tuple:
    """
    Perform headless Chrome login.

    Returns (driver, CDPCapture) on success; (None, None) on failure.
    The CDPCapture is NOT started — caller must call .start() when ready.
    """
    driver = _start_chrome(proxy)
    if driver is None:
        return None, None

    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
    except ImportError:
        log.error("selenium not installed")
        driver.quit()
        return None, None

    W = WebDriverWait(driver, 30)
    try:
        log.info("Opening login page ...")
        driver.get(BASE + "/iec/foservices/#/login")
        time.sleep(4)

        log.info("Entering TAN: %s", tan)
        tan_field = W.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[type='text'], input[placeholder*='PAN']")
        ))
        tan_field.clear()
        tan_field.send_keys(tan)
        time.sleep(0.5)

        log.info("Clicking Continue ...")
        btn = W.until(EC.element_to_be_clickable(
            (By.XPATH,
             "//button[contains(normalize-space(.), 'Continue')]"
             "[not(contains(normalize-space(.), 'Back'))]")
        ))
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(4)

        try:
            W.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[type='password']")
            ))
        except Exception:
            log.error("Password page did not load")
            driver.save_screenshot("login_error.png")
            driver.quit()
            return None, None

        log.info("Ticking SAM checkbox ...")
        _tick_checkbox(driver)
        time.sleep(0.5)

        log.info("Entering password ...")
        pwd = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        pwd.clear()
        pwd.send_keys(password)
        time.sleep(0.5)

        log.info("Submitting login ...")
        for _ in range(8):
            for b in driver.find_elements(By.TAG_NAME, "button"):
                if ("Continue" in b.text
                        and b.is_enabled()
                        and not b.get_attribute("disabled")):
                    driver.execute_script("arguments[0].click();", b)
                    log.info("Clicked Continue")
                    break
            else:
                time.sleep(1)
                continue
            break

        time.sleep(5)
        _handle_dual_login(driver)
        time.sleep(4)

        log.info("Post-submit URL: %s", driver.current_url)
        for tick in range(15):
            if "login" not in driver.current_url.lower():
                log.info("Login SUCCESS")
                break
            if tick == 4:   # snapshot mid-wait for diagnostics
                driver.save_screenshot("login_pending.png")
                log.info("Mid-wait snapshot → login_pending.png")
            time.sleep(2)
        else:
            log.error("Login FAILED — still on: %s", driver.current_url)
            driver.save_screenshot("login_failed.png")
            log.error("Screenshot → login_failed.png  (check for error message / OTP page)")
            driver.quit()
            return None, None

        log.info("Cookies after login: %d", len(driver.get_cookies()))
        return driver, CDPCapture(driver)

    except Exception as exc:
        log.error("Selenium login error: %s", exc)
        try:
            driver.save_screenshot("login_error.png")
            driver.quit()
        except Exception:
            pass
        return None, None


# ── Payment History navigation ─────────────────────────────────────────────────

def open_payment_history_ui(driver) -> bool:
    """
    Navigate to e-File → e-Pay Tax → Payment History.
    This triggers Angular's paymenthistory API call which CDP intercepts.
    Returns True on success.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    log.info("Opening Payment History ...")
    W = WebDriverWait(driver, 40)
    try:
        W.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(3)

        # e-File menu
        efile = W.until(EC.presence_of_element_located(
            (By.XPATH, "//span[normalize-space()='e-File']")
        ))
        driver.execute_script("arguments[0].click();", efile)
        log.info("  Clicked e-File")
        time.sleep(3)

        # e-Pay Tax submenu
        epay = W.until(EC.presence_of_element_located(
            (By.XPATH, "//span[contains(text(),'e-Pay Tax')]")
        ))
        driver.execute_script("arguments[0].click();", epay)
        log.info("  Clicked e-Pay Tax")
        time.sleep(8)

        # Income Tax Act selection dialog
        for attempt in range(3):
            if "Select Applicable Income Tax Act" not in driver.page_source:
                break
            log.info("  Act Selection dialog (attempt %d/3) ...", attempt + 1)
            try:
                act_radio = W.until(EC.presence_of_element_located(
                    (By.XPATH, "//label[contains(.,'Income-tax Act, 1961')]")
                ))
                driver.execute_script("arguments[0].click();", act_radio)
                time.sleep(1)
                cont_btn = W.until(EC.presence_of_element_located(
                    (By.XPATH,
                     "//button[contains(@class,'large-button-primary')"
                     " and normalize-space()='Continue']")
                ))
                driver.execute_script("arguments[0].click();", cont_btn)
                log.info("  Clicked Continue")
                time.sleep(5)
            except Exception as exc:
                log.warning("  Act selection error: %s", exc)

        W.until(lambda d: "Select Applicable Income Tax Act" not in d.page_source)

        # Payment History tab
        history_tab = W.until(EC.element_to_be_clickable(
            (By.XPATH,
             "//div[@role='tab']//span[contains(text(),'Payment History')]")
        ))
        driver.execute_script("arguments[0].click();", history_tab)
        log.info("  Clicked Payment History tab")
        time.sleep(3)

        # Destroy overlay dialogs that can block grid interactions
        driver.execute_script(
            "document.querySelectorAll("
            "'.cdk-overlay-container,.cdk-overlay-backdrop,"
            ".mat-dialog-container,.mat-mdc-dialog-container'"
            ").forEach(function(n){n.remove();});"
        )

        # Wait for ag-Grid to render
        W.until(lambda d: d.execute_script(
            "return (document.querySelectorAll('div[role=\"gridcell\"]').length > 3 || "
            "document.querySelectorAll('div[col-id=\"cinNum\"]').length > 0) && "
            "document.querySelectorAll('.cdk-overlay-container').length === 0;"
        ))
        log.info("Payment History loaded successfully")
        return True

    except Exception as exc:
        log.error("Payment History navigation failed: %s", exc)
        try:
            driver.save_screenshot("nav_error.png")
        except Exception:
            pass
        return False


# ── Session building ───────────────────────────────────────────────────────────

_HOP_BY_HOP = {
    "content-length", "transfer-encoding",
    "connection", "host", "accept-encoding",
}


def build_session(
    driver,
    cdp_headers: dict,
    proxy: str = "",
) -> requests.Session:
    """
    Construct a requests.Session from Selenium cookies + CDP-captured
    Angular request headers.
    """
    sess = requests.Session()

    se_cookies = driver.get_cookies()
    for c in se_cookies:
        sess.cookies.set(
            c["name"], c["value"],
            domain=c.get("domain", ".eportal.incometax.gov.in"),
        )
    log.info("Session: %d cookies injected", len(se_cookies))

    # Strip headers that requests must not forward
    hdrs = {k: v for k, v in cdp_headers.items()
            if k.lower() not in _HOP_BY_HOP}

    hdrs.setdefault("User-Agent",   USER_AGENT)
    hdrs.setdefault("Accept",       "application/json, text/plain, */*")
    hdrs.setdefault("Content-Type", "application/json")

    # Pull XSRF token from cookies if CDP didn't capture it
    if not any(k.upper() == "X-XSRF-TOKEN" for k in hdrs):
        xsrf = next(
            (c["value"] for c in se_cookies
             if c["name"].upper() in ("XSRF-TOKEN", "X-XSRF-TOKEN")),
            None,
        )
        if xsrf:
            hdrs["X-XSRF-TOKEN"] = xsrf
            log.info("X-XSRF-TOKEN injected from cookies")

    sess.headers.update(hdrs)
    log.info("Session headers set: %s", list(hdrs.keys()))

    if proxy:
        sess.proxies = {"https": proxy, "http": proxy}

    return sess


# ── Session persistence ────────────────────────────────────────────────────────

def save_session(session: requests.Session, tan: str, filepath: str) -> None:
    """Persist cookies + headers to a JSON file for reuse on the next run."""
    import time as _time
    data = {
        "tan":      tan,
        "saved_at": _time.time(),
        "cookies":  {k: v for k, v in session.cookies.items()},
        "headers":  dict(session.headers),
    }
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        log.info("Session saved: %s", filepath)
    except Exception as exc:
        log.warning("Session save failed: %s", exc)


def load_session(
    filepath: str,
    tan: str,
    ttl: int = 3600,
) -> Optional[requests.Session]:
    """
    Load a previously saved session.
    Returns None if the file is missing, TAN doesn't match, or the session
    has exceeded its TTL.
    """
    import time as _time
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    if data.get("tan") != tan:
        log.info("Saved session TAN mismatch — discarding")
        return None

    age = _time.time() - data.get("saved_at", 0)
    if age > ttl:
        log.info("Saved session expired (%.0f s old, TTL=%d s)", age, ttl)
        return None

    sess = requests.Session()
    for k, v in data.get("cookies", {}).items():
        sess.cookies.set(k, v, domain=".eportal.incometax.gov.in")
    sess.headers.update(data.get("headers", {}))
    log.info("Loaded saved session (age %.0f s)", age)
    return sess


def validate_session(session: requests.Session, tan: str) -> bool:
    """
    Check if the session is still valid via the dashboard endpoint.
    """
    try:
        url = API_BASE + "/loginapi/auth/dashboard"
        r   = session.get(url, timeout=10)
        if r.status_code == 200:
            body = r.json()
            if body.get("successFlag"):
                log.info("Session validation: OK")
                return True
        log.info("Session validation: FAILED (HTTP %s)", r.status_code)
    except Exception as exc:
        log.info("Session validation error: %s", exc)
    return False


# ── Main entry ─────────────────────────────────────────────────────────────────

def get_session(cfg: dict) -> Tuple:
    """
    Establish an authenticated session.

    Returns (session, driver, cdp_capture):
      - Saved session valid  → (session, None, None)  — Chrome never starts
      - Fresh Selenium login → (session, driver, cdp_capture)
      - Failure              → (None, None, None)

    The caller owns the driver lifecycle — must call driver.quit() and
    cdp_capture.stop() when finished with the browser.
    """
    tan          = cfg["TAN"]
    proxy        = cfg.get("PROXY", "")
    session_file = cfg.get("SESSION_FILE", "tds_session.json")
    session_ttl  = cfg.get("SESSION_TTL", 3600)

    # ── Attempt session reuse (no Chrome needed) ────────────────────────
    saved = load_session(session_file, tan, session_ttl)
    if saved and validate_session(saved, tan):
        log.info("Reusing saved session — Selenium not needed")
        return saved, None, None

    # ── Fresh login ─────────────────────────────────────────────────────
    log.info("Starting fresh Selenium login ...")
    driver, cdp_capture = login(tan, cfg["PASSWORD"], proxy)
    if driver is None:
        return None, None, None

    # Start CDP before dashboard so Angular's saveEntity / other profile
    # API calls are captured during the initial page load.
    log.info("Starting CDP capture ...")
    cdp_capture.start()

    log.info("Navigating to dashboard ...")
    driver.get(BASE + "/iec/foservices/#/dashboard")
    time.sleep(5)

    if not open_payment_history_ui(driver):
        log.error("Payment History navigation failed")
        cdp_capture.stop()
        driver.quit()
        return None, None, None

    time.sleep(3)   # let Angular fire background API calls

    log.info("Waiting for CDP headers ...")
    cdp_headers = cdp_capture.wait_for_api_headers(
        url_fragment="/paymentapi/", timeout=30
    )
    if not cdp_headers:
        log.warning("CDP headers not captured — session may lack auth tokens")
        cdp_headers = {}

    session = build_session(driver, cdp_headers, proxy)

    if cdp_headers:
        save_session(session, tan, session_file)

    return session, driver, cdp_capture

"""
Chrome DevTools Protocol (CDP) capture layer.

Why CDP?
  The ITD portal is an Angular SPA. Its backend returns HTTP 500 for any
  request that doesn't carry the exact Angular-injected headers
  (X-XSRF-TOKEN, AuthToken …).  We cannot replicate those headers manually,
  so we intercept them from a real Angular request via CDP and copy them
  into a requests.Session for all subsequent calls.

CDPCapture
  - Runs a background thread that drains the Selenium performance log
  - Tracks Network.requestWillBeSent  → captures URL + headers
  - Tracks Network.loadingFinished    → marks body as retrievable
  - wait_for_api_headers()  — blocks until headers arrive
  - get_response_body()     — steals an already-loaded response body
                              (must be called from the main Selenium thread)
"""

import json
import time
import base64
import logging
import threading
from typing import Optional

log = logging.getLogger("TDS")


class CDPCapture:
    """
    Thread-safe collector of CDP Network events.

    Typical usage::

        cap = CDPCapture(driver)
        cap.start()
        # ... navigate the page ...
        headers = cap.wait_for_api_headers("/paymentapi/", timeout=30)
        body    = cap.get_response_body("/paymenthistory", driver, timeout=10)
        cap.stop()
    """

    # Intercept only ITD portal API paths; ignore static assets
    _TARGET_PATHS = (
        "/paymentapi/auth/challan/",
        "/paymentapi/auth/",
        "/loginapi/auth/",
        "/servicesapi/auth/",
    )

    def __init__(self, driver):
        self._driver   = driver
        self._lock     = threading.Lock()
        self._stop     = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Each entry: {url, headers, postData, requestId}
        self._captured: list[dict]  = []
        # requestIds whose body is ready to retrieve
        self._finished: set[str]    = set()
        # requestIds whose body has already been consumed
        self._consumed: set[str]    = set()

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Enable CDP network events and begin background polling."""
        try:
            self._driver.execute_cdp_cmd("Network.enable", {})
            log.info("CDP Network.enable OK")
        except Exception as exc:
            log.warning("CDP Network.enable failed: %s", exc)

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="CDPCapture"
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the polling thread to stop and wait for it."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    # ── Internal polling ───────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                for entry in self._driver.get_log("performance"):
                    self._handle_entry(entry)
            except Exception:
                pass
            time.sleep(0.1)

    def _handle_entry(self, entry: dict) -> None:
        try:
            msg    = json.loads(entry["message"])["message"]
            method = msg.get("method", "")
            params = msg.get("params", {})

            if method == "Network.requestWillBeSent":
                req = params.get("request", {})
                url = req.get("url", "")
                if not any(p in url for p in self._TARGET_PATHS):
                    return
                with self._lock:
                    self._captured.append({
                        "url":       url,
                        "headers":   req.get("headers", {}),
                        "postData":  req.get("postData", ""),
                        "requestId": params.get("requestId", ""),
                    })
                    try:
                        with open("cdp_dump.txt", "a") as f:
                            f.write(url + "\n" + str(req.get("postData", "")) + "\n\n")
                    except Exception:
                        pass
                log.info("CDP captured: %s", url)

            elif method in ("Network.loadingFinished",
                             "Network.responseReceived"):
                rid = params.get("requestId", "")
                with self._lock:
                    if any(c["requestId"] == rid for c in self._captured):
                        self._finished.add(rid)

        except Exception:
            pass

    # ── Public query API ───────────────────────────────────────────────

    def wait_for_api_headers(
        self,
        url_fragment: str = "/paymentapi/",
        timeout: int      = 60,
    ) -> Optional[dict]:
        """
        Block until a captured request URL contains url_fragment,
        then return its headers dict.  Returns None on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                for cap in self._captured:
                    if url_fragment in cap["url"]:
                        log.info("CDP headers from: %s", cap["url"])
                        return cap["headers"]
            time.sleep(1)

        log.warning(
            "CDP header capture timeout (%d s) for '%s'", timeout, url_fragment
        )
        return None

    def get_response_body(
        self,
        url_fragment:  str,
        driver,
        timeout:       int           = 60,
        body_contains: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Wait for a matching request to finish loading, then call
        Network.getResponseBody to retrieve its JSON payload.

        IMPORTANT: must be called from the main Selenium thread because
        execute_cdp_cmd is not thread-safe.

        body_contains — only accept a response whose POST body contains
                        this string (useful for matching a specific CRN).
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            target_id = target_url = None
            with self._lock:
                for cap in self._captured:
                    if (url_fragment in cap["url"]
                            and cap["requestId"] in self._finished
                            and cap["requestId"] not in self._consumed):
                        if (body_contains
                                and body_contains not in cap.get("postData", "")):
                            continue
                        target_id  = cap["requestId"]
                        target_url = cap["url"]
                        break

            if target_id:
                with self._lock:
                    self._consumed.add(target_id)   # claim before retrieval
                try:
                    result    = driver.execute_cdp_cmd(
                        "Network.getResponseBody", {"requestId": target_id}
                    )
                    body_text = result.get("body", "")
                    if result.get("base64Encoded"):
                        body_text = base64.b64decode(body_text).decode("utf-8")
                    parsed = json.loads(body_text)
                    log.info("CDP response body from: %s", target_url)
                    return parsed
                except Exception as exc:
                    log.warning("getResponseBody failed: %s", exc)
                    return None

            time.sleep(0.5)

        return None

    def get_all_captured(self) -> list[dict]:
        with self._lock:
            return list(self._captured)

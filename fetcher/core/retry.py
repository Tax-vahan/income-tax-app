"""
Exponential-backoff retry decorator.

Retries on:
  - HTTP 5xx  (requests.HTTPError where status >= 500)
  - Timeouts / connection failures
  - JSON parse errors (ValueError from resp.json())

4xx errors are NOT retried — they indicate a caller mistake.

Example
-------
    from fetcher.core.retry import with_retry

    @with_retry(max_retries=3, base_delay=1.0)
    def call_api(session, url):
        resp = session.post(url, ...)
        resp.raise_for_status()
        return resp.json()
"""

import time
import functools
import logging
import requests

log = logging.getLogger("TDS")


def with_retry(
    max_retries: int   = 3,
    base_delay:  float = 1.0,
    backoff:     float = 2.0,
):
    """
    Decorator factory.

    Delay schedule with defaults (base=1 s, backoff=2):
        attempt 1 → wait 1 s
        attempt 2 → wait 2 s
        attempt 3 → wait 4 s
        attempt 4 → raise
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)

                except requests.exceptions.HTTPError as exc:
                    last_exc = exc
                    status   = exc.response.status_code if exc.response is not None else 0
                    if status < 500:
                        raise                           # 4xx — do not retry
                    if attempt >= max_retries:
                        raise
                    delay = base_delay * (backoff ** attempt)
                    log.warning(
                        "HTTP %s on %s — retry %d/%d in %.1f s",
                        status, fn.__name__, attempt + 1, max_retries, delay,
                    )
                    time.sleep(delay)

                except (requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError) as exc:
                    last_exc = exc
                    if attempt >= max_retries:
                        raise
                    delay = base_delay * (backoff ** attempt)
                    log.warning(
                        "Network error on %s — retry %d/%d in %.1f s: %s",
                        fn.__name__, attempt + 1, max_retries, delay, exc,
                    )
                    time.sleep(delay)

                except ValueError as exc:              # resp.json() parse failure
                    last_exc = exc
                    if attempt >= max_retries:
                        raise
                    delay = base_delay * (backoff ** attempt)
                    log.warning(
                        "JSON parse error on %s — retry %d/%d in %.1f s: %s",
                        fn.__name__, attempt + 1, max_retries, delay, exc,
                    )
                    time.sleep(delay)

            raise last_exc  # unreachable, satisfies type checkers
        return wrapper
    return decorator

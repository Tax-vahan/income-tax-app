"""
Exponential-backoff retry decorator with full jitter.

Retries on:
  - HTTP 5xx  (requests.HTTPError where status >= 500)
  - Timeouts / connection failures
  - JSON parse errors (ValueError from resp.json())

4xx errors are NOT retried — they indicate a caller mistake.

Jitter: each delay is randomised to [0, cap] (full-jitter strategy) so that
multiple concurrent worker threads don't all wake up and hammer the portal at
the same instant after a failure burst.
"""

import time
import random
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

    Delay schedule with defaults — cap doubles each attempt, actual sleep is
    uniform-random in [0, cap] (full jitter, prevents thundering-herd):
        attempt 1 → cap=1 s   → sleep in [0, 1]
        attempt 2 → cap=2 s   → sleep in [0, 2]
        attempt 3 → cap=4 s   → sleep in [0, 4]
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
                    cap   = base_delay * (backoff ** attempt)
                    delay = random.uniform(0, cap)
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
                    cap   = base_delay * (backoff ** attempt)
                    delay = random.uniform(0, cap)
                    log.warning(
                        "Network error on %s — retry %d/%d in %.1f s: %s",
                        fn.__name__, attempt + 1, max_retries, delay, exc,
                    )
                    time.sleep(delay)

                except ValueError as exc:              # resp.json() parse failure
                    last_exc = exc
                    if attempt >= max_retries:
                        raise
                    cap   = base_delay * (backoff ** attempt)
                    delay = random.uniform(0, cap)
                    log.warning(
                        "JSON parse error on %s — retry %d/%d in %.1f s: %s",
                        fn.__name__, attempt + 1, max_retries, delay, exc,
                    )
                    time.sleep(delay)

            raise last_exc  # unreachable, satisfies type checkers
        return wrapper
    return decorator

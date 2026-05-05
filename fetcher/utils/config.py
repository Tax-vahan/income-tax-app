"""
Shared constants and runtime configuration.
Edit CONFIG before running, or pass overrides to run_fetch().
"""
import os

BASE     = "https://eportal.incometax.gov.in"
API_BASE = BASE + "/iec"

# Runtime data directory — /app/data in Docker (mounted volume), ./data locally
_DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "data"))
os.makedirs(_DATA_DIR, exist_ok=True)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)

CONFIG = {
    # ── Credentials ──────────────────────────────────────────────────
    "TAN":          "TAN_NUMBER",
    "PASSWORD":     "PASSWORD",

    # ── Date range ───────────────────────────────────────────────────
    "FROM_DATE":    "01/01/2026",
    "TO_DATE":      "17/04/2026",

    # ── Output ───────────────────────────────────────────────────────
    "OUTPUT_FILE":  "TDS_Challans.xlsx",

    # ── Fetch tuning ─────────────────────────────────────────────────
    "PAGE_SIZE":    10000,   # challans per API page
    "TIMEOUT":      25,      # HTTP request timeout (seconds) — fail fast, then retry
    "WORKERS":      25,      # ThreadPoolExecutor max workers (I/O-bound, so higher is fine)
    "BATCH_SIZE":   20,      # detail-fetch batch size (was 5 — bigger = less overhead)

    # ── Detail fetch strategy ─────────────────────────────────────────
    "FAST_MODE":    True,
    "DETAIL_MODE":  "AUTO",  # AUTO | ALWAYS | SKIP

    # ── Reliability ───────────────────────────────────────────────────
    "RETRY_COUNT":  2,       # jitter makes 2 retries sufficient (was 3)

    # ── Network ───────────────────────────────────────────────────────
    "PROXY":        "",      # e.g. "socks5://127.0.0.1:1080"

    # ── Session reuse ─────────────────────────────────────────────────
    "SESSION_FILE": os.path.join(_DATA_DIR, "tds_session.json"),
    "SESSION_TTL":  7200,    # 2 hours — avoids Selenium cold-starts on repeated calls
}

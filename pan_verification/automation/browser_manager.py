from pathlib import Path
import asyncio
import os
from playwright.async_api import async_playwright, BrowserContext, Playwright

from pan_verification.utils.config import settings
from pan_verification.utils.logger import get_logger

logger = get_logger(__name__)

# Patches the standard automation "tells" that bot-mitigation systems (Akamai,
# DataDome, PerimeterX, Cloudflare Bot Management, etc.) check for client-side.
# Confirmed live against traces-app.tdscpc.gov.in: an identical POST request
# (same headers, body, auth token) succeeds from a genuine Chrome tab but
# 404s from Playwright's default Chromium — the write endpoints are gated by
# something Playwright's default fingerprint trips and GET/read endpoints
# aren't. Run before any page script via add_init_script().
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

window.chrome = window.chrome || { runtime: {} };

Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin' })),
});

Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
});

const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
);
"""


class BrowserManager:
    _instance = None
    _playwright: Playwright | None = None
    _context: BrowserContext | None = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_context(cls) -> BrowserContext:
        async with cls._lock:
            if cls._context is None:
                await cls._init_browser()
            return cls._context

    @classmethod
    async def _init_browser(cls):
        logger.info("Initializing Playwright persistent context...")
        cls._playwright = await async_playwright().start()

        user_data_dir = str(Path(__file__).resolve().parent.parent.parent / ".browser_data")
        os.makedirs(user_data_dir, exist_ok=True)

        cls._context = await cls._playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=settings.HEADLESS,
            viewport={'width': settings.VIEWPORT_WIDTH, 'height': settings.VIEWPORT_HEIGHT},
            timeout=settings.TIMEOUT,
            args=["--disable-blink-features=AutomationControlled"],
            # Headless Chromium reports "HeadlessChrome/..." in its own UA by
            # default — an immediate bot-detection tell. Force a normal UA
            # regardless of settings.HEADLESS.
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        await cls._context.add_init_script(_STEALTH_INIT_SCRIPT)
        logger.info("Playwright persistent context initialized successfully")

    @classmethod
    async def close(cls):
        async with cls._lock:
            if cls._context:
                await cls._context.close()
                cls._context = None
            if cls._playwright:
                await cls._playwright.stop()
                cls._playwright = None
            logger.info("Playwright resources released")

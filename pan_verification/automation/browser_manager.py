from pathlib import Path
import asyncio
import os
from playwright.async_api import async_playwright, BrowserContext, Playwright

from pan_verification.utils.config import settings
from pan_verification.utils.logger import get_logger

logger = get_logger(__name__)


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
            timeout=settings.TIMEOUT
        )
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

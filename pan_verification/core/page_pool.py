import asyncio
from typing import Dict, List, Optional
from playwright.async_api import Page
from pan_verification.utils.logger import get_logger

logger = get_logger(__name__)


class PagePool:
    """
    Manages reusable pages per session.
    Reusing pages significantly improves performance by avoiding page recreation overhead.
    """

    def __init__(self, max_pages_per_session: int = 3):
        self._session_pages: Dict[str, List[Page]] = {}
        self._active_pages: Dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._max_pages = max_pages_per_session
        logger.info(f"PagePool initialized with max {max_pages_per_session} pages per session")

    async def get_page(self, session_id: str, context) -> Page:
        """
        Get a page for the session. Reuses existing page if available, otherwise creates new.
        """
        async with self._lock:
            # Get or create page list for this session
            if session_id not in self._session_pages:
                self._session_pages[session_id] = []
                self._active_pages[session_id] = 0

            pages = self._session_pages[session_id]

            # Return existing unused page if available
            if pages and len(pages) > self._active_pages[session_id]:
                page = pages[self._active_pages[session_id]]
                self._active_pages[session_id] += 1
                logger.debug(f"Reusing page for session {session_id} (active: {self._active_pages[session_id]}/{len(pages)})")
                return page

            # Create new page if under limit
            if len(pages) < self._max_pages:
                try:
                    page = await context.new_page()
                    pages.append(page)
                    self._active_pages[session_id] += 1
                    logger.debug(f"Created new page for session {session_id} (total: {len(pages)})")
                    return page
                except Exception as e:
                    logger.error(f"Failed to create new page: {e}")
                    raise

            # All pages in use, return first page (caller should handle)
            logger.warning(f"All pages in use for session {session_id}, reusing first page")
            page = pages[0]
            return page

    async def release_page(self, session_id: str) -> None:
        """
        Release a page back to the pool (mark as inactive).
        """
        async with self._lock:
            if session_id in self._active_pages and self._active_pages[session_id] > 0:
                self._active_pages[session_id] -= 1
                logger.debug(f"Released page for session {session_id} (active: {self._active_pages[session_id]})")

    async def cleanup_session_pages(self, session_id: str) -> None:
        """
        Close all pages for a session.
        """
        async with self._lock:
            if session_id in self._session_pages:
                pages = self._session_pages[session_id]
                for page in pages:
                    try:
                        await page.close()
                    except Exception as e:
                        logger.warning(f"Error closing page: {e}")

                del self._session_pages[session_id]
                del self._active_pages[session_id]
                logger.info(f"Cleaned up {len(pages)} pages for session {session_id}")

    async def cleanup_all(self) -> None:
        """
        Close all pages across all sessions.
        """
        async with self._lock:
            total_pages = 0
            for session_id, pages in self._session_pages.items():
                for page in pages:
                    try:
                        await page.close()
                    except Exception:
                        pass
                total_pages += len(pages)

            self._session_pages.clear()
            self._active_pages.clear()
            logger.info(f"Cleaned up all {total_pages} pages")

    def get_stats(self, session_id: Optional[str] = None) -> Dict:
        """
        Get page pool statistics.
        """
        if session_id:
            if session_id not in self._session_pages:
                return {"session_id": session_id, "pages": 0, "active": 0}
            return {
                "session_id": session_id,
                "pages": len(self._session_pages[session_id]),
                "active": self._active_pages.get(session_id, 0)
            }

        total_pages = sum(len(pages) for pages in self._session_pages.values())
        total_active = sum(self._active_pages.values())

        return {
            "total_sessions": len(self._session_pages),
            "total_pages": total_pages,
            "total_active": total_active
        }


# Global page pool instance
_page_pool: Optional[PagePool] = None


def get_page_pool() -> PagePool:
    """Get or create the global page pool."""
    global _page_pool
    if _page_pool is None:
        _page_pool = PagePool()
    return _page_pool


async def cleanup_page_pool() -> None:
    """Cleanup the global page pool."""
    global _page_pool
    if _page_pool:
        await _page_pool.cleanup_all()
        _page_pool = None

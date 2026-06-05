import asyncio
import time
import uuid
from typing import Dict, Optional
from pan_verification.utils.logger import get_logger

logger = get_logger(__name__)


class SessionData:
    """Represents a user session with data and lifecycle."""

    def __init__(self, session_id: str, ttl: int):
        self.session_id = session_id
        self.created_at = time.time()
        self.expires_at = self.created_at + ttl
        self.data: Dict = {}
        self.page = None
        self.last_accessed = self.created_at

    def is_expired(self) -> bool:
        """Check if session has expired."""
        return time.time() > self.expires_at

    def is_active(self) -> bool:
        """Check if session is active (has data)."""
        return bool(self.data)

    def refresh_expiry(self, ttl: int) -> None:
        """Extend session expiry time."""
        self.expires_at = time.time() + ttl
        self.last_accessed = time.time()

    def cleanup(self) -> None:
        """Clean up session resources."""
        self.data.clear()
        self.page = None


class ThreadSafeSessionManager:
    """Thread-safe manager for user sessions."""

    def __init__(self, ttl: int = 1800, cleanup_interval: int = 300):
        self._sessions: Dict[str, SessionData] = {}
        self._tan_to_session: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl
        self._cleanup_interval = cleanup_interval
        self._cleanup_task: Optional[asyncio.Task] = None

    async def create_session(self) -> str:
        """Create a new session and return session ID."""
        session_id = str(uuid.uuid4())
        async with self._lock:
            self._sessions[session_id] = SessionData(session_id, self._ttl)
            logger.info(f"Session created: {session_id}")
        return session_id

    async def get_session(self, session_id: str) -> Optional[SessionData]:
        """Get session by ID, returns None if expired or not found."""
        async with self._lock:
            if session_id not in self._sessions:
                logger.warning(f"Session not found: {session_id}")
                return None

            session = self._sessions[session_id]

            if session.is_expired():
                logger.info(f"Session expired: {session_id}")
                await self._cleanup_session(session_id)
                return None

            session.refresh_expiry(self._ttl)
            return session

    async def update_session(self, session_id: str, data: Dict) -> bool:
        """Update session data."""
        async with self._lock:
            if session_id not in self._sessions:
                logger.warning(f"Session not found for update: {session_id}")
                return False

            session = self._sessions[session_id]

            if session.is_expired():
                logger.info(f"Cannot update expired session: {session_id}")
                await self._cleanup_session(session_id)
                return False

            session.data.update(data)
            
            tan = session.data.get("tan")
            if tan:
                self._tan_to_session[tan] = session_id
                
            session.refresh_expiry(self._ttl)
            logger.info(f"Session updated: {session_id}")
            return True

    async def get_session_by_tan(self, tan: str) -> Optional[str]:
        """Get the active session_id for a given TAN, if any."""
        async with self._lock:
            session_id = self._tan_to_session.get(tan)
            if not session_id:
                return None
                
            session = self._sessions.get(session_id)
            if not session or session.is_expired():
                # Cleanup if expired
                if session_id in self._sessions:
                    await self._cleanup_session(session_id)
                elif tan in self._tan_to_session:
                    del self._tan_to_session[tan]
                return None
                
            return session_id

    async def set_page(self, session_id: str, page) -> bool:
        """Associate a page with a session."""
        async with self._lock:
            if session_id not in self._sessions:
                logger.warning(f"Session not found for page assignment: {session_id}")
                return False

            session = self._sessions[session_id]

            if session.is_expired():
                logger.info(f"Cannot set page on expired session: {session_id}")
                await self._cleanup_session(session_id)
                return False

            session.page = page
            logger.debug(f"Page assigned to session: {session_id}")
            return True

    async def get_page(self, session_id: str):
        """Get page associated with session."""
        async with self._lock:
            if session_id not in self._sessions:
                return None

            session = self._sessions[session_id]

            if session.is_expired():
                await self._cleanup_session(session_id)
                return None

            return session.page

    async def clear_page(self, session_id: str) -> bool:
        """Clear page from session."""
        async with self._lock:
            if session_id not in self._sessions:
                return False

            session = self._sessions[session_id]
            session.page = None
            logger.debug(f"Page cleared from session: {session_id}")
            return True

    async def validate_session(self, session_id: str) -> bool:
        """Validate if session exists and is active."""
        session = await self.get_session(session_id)
        if session is None:
            return False
        return session.is_active()

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        async with self._lock:
            if session_id not in self._sessions:
                return False

            session = self._sessions[session_id]
            session.cleanup()

            if session.page:
                try:
                    await session.page.close()
                except Exception as e:
                    logger.warning(f"Error closing page for session {session_id}: {e}")

            del self._sessions[session_id]
            logger.info(f"Session deleted: {session_id}")
            return True

    async def _cleanup_session(self, session_id: str) -> None:
        """Internal method to cleanup session without lock."""
        session = self._sessions.get(session_id)
        if session:
            tan = session.data.get("tan")
            if tan and self._tan_to_session.get(tan) == session_id:
                del self._tan_to_session[tan]
                
            session.cleanup()
            if session.page:
                try:
                    await session.page.close()
                except Exception as e:
                    logger.warning(f"Error closing page during cleanup: {e}")
            del self._sessions[session_id]

    async def cleanup_expired_sessions(self) -> int:
        """Clean up all expired sessions. Returns number of sessions cleaned."""
        async with self._lock:
            expired_ids = [
                sid for sid, session in self._sessions.items()
                if session.is_expired()
            ]

            for session_id in expired_ids:
                await self._cleanup_session(session_id)

            if expired_ids:
                logger.info(f"Cleaned up {len(expired_ids)} expired sessions")

            return len(expired_ids)

    async def start_background_cleanup(self) -> None:
        """Start background task to periodically clean expired sessions."""
        if self._cleanup_task is not None:
            return

        async def cleanup_loop():
            try:
                while True:
                    await asyncio.sleep(self._cleanup_interval)
                    await self.cleanup_expired_sessions()
            except asyncio.CancelledError:
                logger.info("Background cleanup task cancelled")
            except Exception as e:
                logger.error(f"Error in background cleanup: {e}")

        self._cleanup_task = asyncio.create_task(cleanup_loop())
        logger.info("Background cleanup task started")

    async def stop_background_cleanup(self) -> None:
        """Stop background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Background cleanup task stopped")

    async def close(self) -> None:
        """Close all sessions and cleanup resources."""
        await self.stop_background_cleanup()

        async with self._lock:
            session_ids = list(self._sessions.keys())

        for session_id in session_ids:
            await self.delete_session(session_id)

        logger.info("Session manager closed")

    def get_stats(self) -> Dict:
        """Get session statistics."""
        total = len(self._sessions)
        active = sum(1 for s in self._sessions.values() if s.is_active() and not s.is_expired())
        expired = sum(1 for s in self._sessions.values() if s.is_expired())

        return {
            "total_sessions": total,
            "active_sessions": active,
            "expired_sessions": expired
        }


# Global session manager instance
_session_manager: Optional[ThreadSafeSessionManager] = None


async def get_session_manager() -> ThreadSafeSessionManager:
    """Get or create the global session manager."""
    global _session_manager
    if _session_manager is None:
        _session_manager = ThreadSafeSessionManager()
        await _session_manager.start_background_cleanup()
    return _session_manager


async def close_session_manager() -> None:
    """Close the global session manager."""
    global _session_manager
    if _session_manager:
        await _session_manager.close()
        _session_manager = None

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pan_verification.api.endpoints import router as api_router
from pan_verification.automation.browser_manager import BrowserManager
from pan_verification.core import get_session_manager, close_session_manager
from pan_verification.core.page_pool import cleanup_page_pool
from pan_verification.utils.config import settings
from pan_verification.utils.logger import get_logger, configure_logging
from pan_verification.utils.errors import AutomationError, map_automation_error_to_http
from pan_verification.utils.selectors import SelectorManager

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("=" * 50)
    logger.info("Starting TRACES PAN Verification Service...")
    logger.info("=" * 50)

    try:
        # Initialize logging
        configure_logging(settings.LOG_LEVEL)
        logger.info(f"Logging level: {settings.LOG_LEVEL}")

        # Load selectors configuration
        SelectorManager.load_selectors(settings.SELECTORS_PATH)
        logger.info(f"Selectors loaded from {settings.SELECTORS_PATH}")

        # Initialize browser manager
        logger.info("Initializing browser manager...")
        await BrowserManager.get_context()
        logger.info("Browser manager initialized successfully")

        # Initialize session manager
        logger.info("Initializing session manager...")
        session_mgr = await get_session_manager()
        logger.info("Session manager initialized with background cleanup")

        logger.info("=" * 50)
        logger.info("Service startup complete")
        logger.info("=" * 50)

    except Exception as e:
        logger.error(f"Error during startup: {str(e)}", exc_info=True)
        raise

    yield

    # Shutdown
    logger.info("=" * 50)
    logger.info("Shutting down TRACES PAN Verification Service...")
    logger.info("=" * 50)

    try:
        # Close browser manager
        logger.info("Closing browser manager...")
        await BrowserManager.close()

        # Close session manager
        logger.info("Closing session manager...")
        await close_session_manager()

        # Cleanup page pool
        logger.info("Cleaning up page pool...")
        await cleanup_page_pool()

        logger.info("=" * 50)
        logger.info("Service shutdown complete")
        logger.info("=" * 50)

    except Exception as e:
        logger.error(f"Error during shutdown: {str(e)}", exc_info=True)


app = FastAPI(
    title="TRACES PAN Verification Service",
    description="Automated service for verifying PAN on TRACES portal",
    version="2.0.0",
    lifespan=lifespan
)

# Register routes
app.include_router(api_router)


@app.exception_handler(AutomationError)
async def automation_exception_handler(request: Request, exc: AutomationError):
    logger.error(f"Automation error: {type(exc).__name__} - {str(exc)}")
    http_exc = map_automation_error_to_http(exc)
    return JSONResponse(
        status_code=http_exc.status_code,
        content={"detail": http_exc.detail}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unexpected error: {type(exc).__name__} - {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Check logs for details."}
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        session_mgr = await get_session_manager()
        stats = session_mgr.get_stats()
        return {
            "status": "healthy",
            "sessions": stats
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

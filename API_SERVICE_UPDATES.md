# API Service Updates for TBR Module

## Changes to Make in api_service.py

### 1. Add TBR Service import (after line 26)
```python
from tbr.services.tbr_service import TBRService, TBRServiceFactory
```

### 2. Add TBR requests file path (after line 33)
```python
TBR_REQUESTS_FILE = os.path.join(DATA_DIR, "tbr_requests.json")
```

### 3. Update lifespan function (after line 76, before yield)
```python
    # TBR Service Initialization
    try:
        logger.info("Initializing TBR Service...")
        tbr_service = TBRService(
            tbr_file=TBR_REQUESTS_FILE,
            jobs_lock=jobs_lock,
            data_dir=DATA_DIR,
            downloads_dir=DOWNLOADS_DIR
        )
        TBRServiceFactory.set_instance(tbr_service)
        app.state.tbr_service = tbr_service
        logger.info("TBR Service initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize TBR Service: {e}", exc_info=True)
```

## Complete Updated Lifespan Function

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting TDS background workers...")
    _stop_event.clear()
    
    app.state.jobs = jobs
    app.state.jobs_lock = jobs_lock
    app.state.job_queue = _job_queue
    app.state.stop_event = _stop_event
    
    workers = []
    for _i in range(_WORKER_COUNT):
        t = threading.Thread(
            target=_worker, daemon=True, name=f"JobWorker-{_i + 1}"
        )
        t.start()
        workers.append(t)
    
    app.state.workers = workers
    
    # PAN Verification Startup
    try:
        logger.info("Initializing PAN Verification BrowserManager...")
        await BrowserManager.get_context()
        logger.info("Initializing PAN Verification SessionManager...")
        session_mgr = await get_session_manager()
        
        app.state.browser_manager = BrowserManager
        app.state.session_manager = session_mgr
        app.state.pan_enabled = True
        logger.info("PAN Verification components initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize PAN Verification components: {e}", exc_info=True)
        app.state.pan_enabled = False

    # TBR Service Initialization ← NEW
    try:
        logger.info("Initializing TBR Service...")
        tbr_service = TBRService(
            tbr_file=TBR_REQUESTS_FILE,
            jobs_lock=jobs_lock,
            data_dir=DATA_DIR,
            downloads_dir=DOWNLOADS_DIR
        )
        TBRServiceFactory.set_instance(tbr_service)
        app.state.tbr_service = tbr_service
        logger.info("TBR Service initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize TBR Service: {e}", exc_info=True)

    yield
    
    # Shutdown
    # PAN Verification Cleanup
    if getattr(app.state, "pan_enabled", False):
        try:
            logger.info("Closing PAN Verification BrowserManager...")
            await BrowserManager.close()
            logger.info("Closing PAN Verification SessionManager...")
            await close_session_manager()
            logger.info("Cleaning up PAN Verification page pool...")
            await cleanup_page_pool()
        except Exception as e:
            logger.error(f"Error during PAN Verification cleanup: {e}", exc_info=True)

    logger.info("Shutting down TDS background workers...")
    _stop_event.set()
    
    # Push sentinel values to unblock workers waiting on queue.get()
    for _ in range(_WORKER_COUNT):
        _job_queue.put(None)
        
    for t in workers:
        t.join(timeout=5.0)
        
    _flush_jobs()
    
    # TBR Service Cleanup (auto-saves via debounce)
    if hasattr(app.state, "tbr_service"):
        try:
            logger.info("Saving TBR requests...")
            app.state.tbr_service._save_requests()
            logger.info("TBR requests saved.")
        except Exception as e:
            logger.error(f"Error saving TBR requests: {e}")
    
    logger.info("Shutdown complete.")
```

## Key Points

✅ TBRService uses the same `jobs_lock` as existing jobs
✅ Shares DATA_DIR and DOWNLOADS_DIR with existing code
✅ Auto-saves to `data/tbr_requests.json` on shutdown
✅ Thread-safe access via threading.Lock()
✅ Compatible with existing patterns (pan_verification, fetcher)


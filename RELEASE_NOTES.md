# Release Notes (v6.0)

## PAN Verification Integration & Unified Backend

Release 6.0 introduces the complete merger of the legacy TDS Challan API backend with the new PAN Verification application, consolidating them into a single, highly resilient FastAPI artifact.

### Core Enhancements
- **Unified FastAPI Lifespan**: Replaced legacy daemon threading mechanisms with the modern `FastAPI lifespan` paradigm. Graceful startup and shutdown events are now perfectly orchestrated.
- **Dual-Browser Automation Architecture**: The system now executes both Selenium (for TDS CDP tracking) and Playwright (for rapid PAN validation) inside the exact same Docker container.
- **Fail-Safe PAN Error Isolation**: Introduced a circuit breaker around the PAN components. If the Playwright binaries crash or are missing from the Docker image, the application gracefully degrades. The PAN endpoints automatically respond with `503 Service Unavailable`, but the TDS components remain fully online and unaffected.
- **Unified Health Checks**: Added `GET /health` root endpoint that intelligently merges the health statuses of the TDS background workers with the Playwright Browser Manager.
- **Unified OpenAPI Schema**: Generated unified Swagger UI documentation available directly at `/docs` separating operational scopes using strict Router tagging.

### Breaking Changes
- **Legacy `pan-verification/` Directory Deprecation**: The standalone `pan-verification` project directory has been deleted. Its logic was natively ported into the root `pan_verification/` python module.
- `selectors.json` has been relocated to `pan_verification/config/selectors.json`.

### Technical Improvements
- Docker build process overhauled to natively pull `playwright install chromium --with-deps` inside the Python layer.
- Thread queue termination upgraded to natively utilize Sentinel passing rather than abrupt `sys.exit` halts, guaranteeing Excel files and JSON outputs are completely flushed to disk on SIGTERM.

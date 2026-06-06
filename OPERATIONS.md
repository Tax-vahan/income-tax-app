# Operations Guide

## 1. Health Checks & Monitoring
The unified API exposes a primary diagnostic endpoint:
```http
GET /health
```
If the container is functioning normally, it returns:
```json
{
  "status": "healthy",
  "services": {
    "tds": "healthy",
    "pan_verification": "healthy"
  }
}
```
**Alerting Strategy**: If the overall `"status"` shifts to `"degraded"`, check the underlying services object. If `pan_verification` is `unavailable`, it means Playwright crashed or failed to boot, however, TDS services will continue to function safely.

## 2. Queue Diagnostics
To monitor the internal TDS background threading queues:
```http
GET /tds/api/v1/queue
```
This returns real-time introspection into pending jobs, running jobs, and worker capacity limits.

## 3. Log Locations
Logging is pushed to `stdout`.
- FastApi and Uvicorn tracebacks are standard.
- PAN Verification specific events are logged under the `pan_verification.*` namespace.
- TDS background events are emitted under the `TDS_API` logger.

## 4. Incident Recovery & Restart Procedures
If the service halts unexpectedly, you do not need to manually purge the queue files.
1. The `jobs.json` dynamically records the final "pending" states of any fetched task.
2. If you restart the container via `docker restart tds-pan-api`, the app natively reads `jobs.json` and immediately re-ingests any stranded jobs back into the `_job_queue` during startup.

## 5. Captcha Verification Debugging
If users continually report failures during `/login/complete`:
1. The OCR model (`ddddocr`) might be struggling with a new Captcha format released by the TRACES portal.
2. Inspect the base64 dumps in the `downloads/` directory if debug logging is enabled.
3. Fallback requires users to execute the 2-step manual captcha extraction via the `/login/init` -> User Input -> `/login/complete` flow.

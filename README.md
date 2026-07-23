# TDS Challan & PAN Verification Unified API Service

This repository contains the unified backend application for TDS Challan Fetching and PAN Verification. It is a highly robust FastAPI application designed to concurrently orchestrate automated browser tasks without resource collisions.

## Project Overview
The system elegantly combines two historically separate microservices into a single backend API:
1. **TDS Challan Service**: Automates interactions with the TRACES portal to fetch tax deductee records and entity profiles.
2. **PAN Verification Service**: Automates PAN verification utilizing OCR and a standalone headless browser layer.

## Features
- **TDS Automation**: Rapid queue-based data fetching utilizing pre-cached sessions.
- **PAN Verification**: End-to-end PAN validation through Captcha bypassing and multi-step verification.
- **Background Processing**: Isolated daemon worker threads ensure zero blocking on the main FastAPI event loop.
- **Dual-Browser Automation**: Runs Selenium alongside Playwright in perfect isolation.
- **Health Monitoring**: Consolidated diagnostics and graceful degradation mechanisms.

## Architecture
The application is governed by a unified **FastAPI Lifespan**.
- The `lifespan` orchestrates the initialization and graceful destruction of both Selenium thread locks and Playwright context managers.
- **Selenium Stack**: Drives the TDS Challenger. Managed via standard threading primitives to throttle concurrency.
- **Playwright Stack**: Drives the PAN Verification. Managed via `asyncio` context managers (`BrowserManager` and `SessionManager`).

Both stacks are actively encapsulated to ensure a Playwright binary failure will never cascade into a TDS service outage.

## Quick Start & Installation

### Local Development
```bash
# 1. Install dependencies
pip install -r requirements.txt
playwright install chromium

# 2. Configure environment
cp .env.example .env
# Edit .env with your required credentials

# 3. Boot the application
uvicorn api_service:app --reload --port 8001
```

### Docker Deployment
```bash
# Build the unified image
docker build -t tds-pan-api .

# Run the container
docker run -d -p 8001:8001 --name tds-backend tds-pan-api
```

## Configuration

### Environment Variables
Configure the application via a `.env` file at the root of the project:
- `HEADLESS`: `true` or `false`
- `TRACES_URL`: Target endpoint (e.g., `https://traces.tdscpc.gov.in`)
- `TIMEOUT`: Operation timeout (ms).

### Architecture specific paths
- `pan_verification/config/selectors.json`: Defines DOM selectors for Playwright.
- `data/`: Storage directory for jobs and entity JSON responses.
- `downloads/`: Storage directory for raw Excel artifacts.

## API Documentation

All routes are visible on the Swagger UI located at `http://localhost:8001/docs`.

### Health Endpoint
```http
GET /health
```
Response:
```json
{
  "status": "healthy",
  "services": {
    "tds": "healthy",
    "pan_verification": "healthy"
  }
}
```

### TDS Endpoints
- `POST /tds/api/v1/fetch`: Queues a background fetch operation.
- `GET /tds/api/v1/jobs/{job_id}`: Polls fetch completion status.
- `POST /tds/api/v1/entity`: Retrieves the Deductor entity profile.
- `POST /tds/api/v1/challan/verify`: Given `{tan, password, deductorId, financialYear, quarter, categoryId}`, queues a background job that fetches manually entered challans from the main TaxVahan API (`api.taxvahan.com/api/challan/fetch`), filters out anything with a `SectionCode` or `BookEntry = Yes`, compares the rest against TRACES data for the date range they span, and returns each challan's status — `Verified`, `Amount Not Verified`, or `Not Found` — via the same job-polling mechanism as `/fetch`.

### PAN Verification Endpoints
- `POST /pan-verification/login/init`: Yields a Session ID and a base64 Captcha.
- `POST /pan-verification/login/complete`: Consumes the solved Captcha.
- `POST /pan-verification/pan/verify`: Validates a single PAN string.
- `POST /pan-verification/pan/bulk-upload`: Submits a CSV for bulk validation.

## Production Recommendations & Troubleshooting

### Memory Requirements
**CRITICAL**: The dual-browser architecture requires a minimum of **1.5 GB of RAM** in production to prevent OS OOMKills. 

### Browser Architecture Context
- **Selenium + Debian Chromium**: Used heavily by the TDS stack due to legacy automation compatibility and predictable CDP (Chrome DevTools Protocol) sniffing requirements.
- **Playwright + Microsoft Chromium**: Leveraged by the new PAN Verification stack for faster modern `asyncio` operations. 

### Troubleshooting
- **Playwright Chromium Errors**: Run `playwright install chromium` inside your local python environment.
- **Zombie Browsers**: Check your container's process list. The FastAPI lifespan guarantees graceful `BrowserManager.close()`, but unhandled SIGKILLs could theoretically strand processes. 

For advanced troubleshooting, refer to `OPERATIONS.md`.

# TDS Challan Fetcher API

A production-grade service that fetches TDS Challan data from the Income Tax Department (ITD) portal using Selenium + Chrome DevTools Protocol (CDP) + `requests.Session`.

---

## Architecture

```
fetcher/
├── main.py                   # Public entry point + CLI + DOM scraper fallback
├── core/
│   ├── auth.py               # Selenium login, session save/load/validate
│   ├── cdp.py                # CDP network event capture
│   ├── api.py                # ITD API calls (requests.Session only)
│   ├── enrich.py             # Data normalisation, date parsing, enrichment
│   └── retry.py              # @with_retry exponential backoff decorator
├── services/
│   ├── challan_service.py    # Orchestration, CircuitBreaker, parallel detail fetch
│   └── excel_service.py      # 4-sheet .xlsx + JSON export
└── utils/
    ├── config.py             # CONFIG dict + shared constants
    └── logger.py             # Centralised structured logging
```

---

## Quick Start

### Docker (recommended)

```bash
# 1. Clone and enter the directory
cd tds_api/

# 2. Start the API
docker compose up -d

# 3. Check it's healthy
curl http://localhost:8001/tds/api/health

# 4. Submit a fetch job
curl -X POST http://localhost:8001/tds/api/v1/fetch \
  -H "Content-Type: application/json" \
  -d '{"tan":"XXXX12345X","password":"yourpw","from_date":"01/01/2026","to_date":"31/03/2026"}'

# 5. Poll job status
curl http://localhost:8001/tds/api/v1/jobs/<job_id>

# 6. Download Excel
curl -OJ http://localhost:8001/tds/api/v1/jobs/<job_id>/download
```

### Local (without Docker)

```bash
pip install -r requirements.txt

# Run as API
uvicorn api_service:app --host 0.0.0.0 --port 8001

# Or run CLI directly
python -m fetcher.main \
  --tan XXXX12345X \
  --password yourpw \
  --from-date 01/01/2026 \
  --to-date 31/03/2026 \
  --detail-mode AUTO
```

---

## Configuration

Edit `fetcher/utils/config.py` or pass overrides via CLI flags / `cfg_override` dict.

| Key | Default | Description |
|---|---|---|
| `WORKERS` | `15` | Parallel threads for detail fetch |
| `BATCH_SIZE` | `5` | Detail fetch batch size |
| `DETAIL_MODE` | `AUTO` | `AUTO` / `ALWAYS` / `SKIP` |
| `RETRY_COUNT` | `3` | API retries per call |
| `SESSION_TTL` | `3600` | Session cache lifetime (seconds) |
| `TIMEOUT` | `30` | HTTP request timeout |

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/tds/api/v1/fetch` | Submit a challan fetch job |
| `GET` | `/tds/api/v1/jobs/{id}` | Poll job status |
| `GET` | `/tds/api/v1/jobs/{id}/download` | Download Excel result |
| `GET` | `/tds/api/v1/jobs` | List all jobs |
| `GET` | `/tds/api/health` | Health check |

### Request Body (`POST /fetch`)
```json
{
  "tan":       "XXXX12345X",
  "password":  "yourpassword",
  "from_date": "01/01/2026",
  "to_date":   "31/03/2026"
}
```

---

## Performance

| Scenario | Estimated Time |
|---|---|
| Cold run (full Selenium login) | ~20–25 seconds |
| Warm run (session cached) | **~8–12 seconds** |
| With `DETAIL_MODE=SKIP` | ~5–8 seconds |

---

## Requirements

- Python 3.11+
- Google Chrome stable
- ChromeDriver (matching Chrome version)
- See `requirements.txt`

# TBR Module Setup Checklist

## ✅ What's Already Done

### TBR Module Files (10 files created)
- [x] `tbr/__init__.py` - Package init
- [x] `tbr/api/__init__.py` - API subpackage
- [x] `tbr/api/endpoints.py` - 4 REST endpoints ✅ Ready to use
- [x] `tbr/models/__init__.py` - Models subpackage
- [x] `tbr/models/schemas.py` - 8 Pydantic schemas ✅ Ready to use
- [x] `tbr/services/__init__.py` - Services subpackage
- [x] `tbr/services/tbr_service.py` - Business logic ✅ UPDATED for JSON
- [x] `tbr/utils/__init__.py` - Utils subpackage
- [x] `tbr/utils/logger.py` - Logger utility
- [x] `tbr/core/__init__.py` - Core subpackage

### Documentation Files
- [x] `ANALYSIS_AND_FIXES.md` - Analysis of patterns
- [x] `API_SERVICE_UPDATES.md` - Exact code to add
- [x] `FIXES_APPLIED.md` - Summary of changes
- [x] `SETUP_CHECKLIST.md` - This file
- [x] `INTEGRATION_SUMMARY.md` - Quick ref
- [x] `TBR_INTEGRATION.md` - Complete guide

## ⏳ What YOU Need to Do (3 small edits)

### Step 1: Add Import
**File**: `api_service.py`
**Line**: After line 26 (after the pan_verification imports)
**Add**:
```python
from tbr.services.tbr_service import TBRService, TBRServiceFactory
```

### Step 2: Add TBR File Path
**File**: `api_service.py`
**Line**: After line 33 (after FILED_FORMS_DIR definition)
**Add**:
```python
TBR_REQUESTS_FILE = os.path.join(DATA_DIR, "tbr_requests.json")
```

### Step 3: Initialize TBR Service
**File**: `api_service.py`
**Location**: In the `lifespan` function

#### 3a. Add startup code (after line 76, before `yield`)
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

#### 3b. Add shutdown code (before line 104, before final `logger.info("Shutdown complete.")`)
```python
    # TBR Service Cleanup
    if hasattr(app.state, "tbr_service"):
        try:
            logger.info("Saving TBR requests...")
            app.state.tbr_service._save_requests()
            logger.info("TBR requests saved.")
        except Exception as e:
            logger.error(f"Error saving TBR requests: {e}")
```

## 🧪 Verification Steps

After making the 3 edits:

### 1. Check Python Syntax
```bash
python -m py_compile income-tax-app/api_service.py
```
Should have NO errors.

### 2. Start the App
```bash
cd income-tax-app
docker-compose up
```
Look for log lines:
```
Initializing TBR Service...
TBR Service initialized successfully.
```

### 3. Test Endpoints
```bash
# Test validate endpoint
curl -X POST http://localhost:8000/api/v1/tbr/validate-tan \
  -H "Content-Type: application/json" \
  -d '{"tan":"PTLA13241E","finYear":"2026","quarter":"3"}'

# Should return 200 with:
# {"isValid": true, "message": "Statement available", ...}
```

### 4. View Interactive API Docs
Open browser: http://localhost:8000/docs
- Should see `/api/v1/tbr/*` endpoints

## 📂 Verify File Structure

```bash
ls -la income-tax-app/tbr/
# Should show:
# __init__.py
# api/
# models/
# services/
# utils/
# core/

ls -la income-tax-app/data/
# After first run, should include:
# jobs.json (existing)
# tbr_requests.json (new, auto-created)
```

## ✅ Success Checklist

- [ ] Made 3 edits to api_service.py
- [ ] No Python syntax errors
- [ ] App starts without TBR initialization errors
- [ ] Can make POST request to `/api/v1/tbr/validate-tan`
- [ ] Can make GET request to `/api/v1/tbr/ready-download-requests`
- [ ] `data/tbr_requests.json` gets created
- [ ] Interactive docs show TBR endpoints at `/docs`

## 🎯 Next: Production Use

Once working, you can:

1. **Integrate with PAN verification** - Reuse authenticated user context
2. **Add to background job queue** - Use existing worker pattern to process TBRs
3. **Generate files** - Add Excel/PDF generation to completed TBRs
4. **Link with fetcher** - Use existing `run_fetch` to get transaction data

## 💡 Troubleshooting

### Error: "TBRService not initialized"
- Verify the 3 edits were made correctly
- Check that `TBRServiceFactory.set_instance()` is called in startup

### Error: "Permission denied on data/tbr_requests.json"
- Check `data/` directory permissions
- Ensure Docker container has write access to volume

### API returns 404 on `/api/v1/tbr/*`
- Verify router is included in api_service.py (line 115)
- Check TBR router import is correct

---

**Ready to go?** Make the 3 edits and test!

**Questions?** Check:
- `FIXES_APPLIED.md` - Complete explanation
- `API_SERVICE_UPDATES.md` - Code snippets ready to copy
- `TBR_INTEGRATION.md` - Full integration guide

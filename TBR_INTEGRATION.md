# TBR (Transaction Based Report) Module Integration

This document describes the TBR module integrated into the income-tax-app project.

## Project Structure

```
income-tax-app/
├── api_service.py                 # Main FastAPI app (TBR router added)
├── tbr/                           # NEW: TBR Module
│   ├── __init__.py
│   ├── api/
│   │   ├── __init__.py
│   │   └── endpoints.py          # REST endpoints (routes)
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py            # Pydantic request/response schemas
│   ├── services/
│   │   ├── __init__.py
│   │   └── tbr_service.py        # Business logic & service layer
│   ├── utils/
│   │   ├── __init__.py
│   │   └── logger.py             # Logging utility
│   └── core/
│       └── __init__.py
├── pan_verification/              # Existing PAN verification module
├── fetcher/                       # Existing fetcher module
├── requirements.txt               # Dependencies
├── api_service.py                # Main app
└── docker-compose.yml            # Docker setup
```

## TBR Module Components

### 1. **Schemas** (`tbr/models/schemas.py`)
Request/response data models using Pydantic:
- `TBRValidationRequest` - Validate TAN request
- `TANValidationResponse` - Validation response
- `TransactionBasedReportDTO` - TBR record DTO
- `PaginatedTBRResponse` - Paginated list response
- `InitiateTBRRequest` - Initiate TBR request
- `TBRStatusResponse` - Status check response

### 2. **Services** (`tbr/services/tbr_service.py`)
Business logic layer:
- `TBRService` - Main service class
  - `validate_tan()` - Check if statement available
  - `initiate_tbr_request()` - Create new TBR request
  - `get_ready_requests()` - List completed TBRs
  - `get_status()` - Check TBR status
- `TBRServiceFactory` - Service factory pattern
- `get_tbr_service()` - FastAPI dependency

### 3. **API Endpoints** (`tbr/api/endpoints.py`)
REST endpoints (auto-prefixed with `/api/v1/tbr`):
- `POST /api/v1/tbr/validate-tan` - Validate TAN
- `GET /api/v1/tbr/ready-download-requests?tanId=...` - Get completed TBRs
- `POST /api/v1/tbr/initiate` - Initiate new TBR
- `GET /api/v1/tbr/status/{request_id}` - Get TBR status

## Integration Points

### 1. **Main App** (`api_service.py`)
TBR router is included in the main FastAPI app:
```python
from tbr.api.endpoints import router as tbr_router
app.include_router(tbr_router, prefix="/api/v1", tags=["TBR"])
```

### 2. **Router Registration**
Available at `/api/v1/tbr/*` endpoints

### 3. **Dependency Injection**
Uses FastAPI's dependency system:
```python
@router.post("/validate-tan")
async def validate_tan(
    request: TBRValidationRequest,
    service: TBRService = Depends(get_tbr_service),
):
```

## API Endpoints

### Validate TAN
```bash
POST /api/v1/tbr/validate-tan
Content-Type: application/json

{
  "tan": "PTLA13241E",
  "finYear": "2026",
  "quarter": "3"
}

Response:
{
  "isValid": true,
  "message": "Statement available",
  "tan": "PTLA13241E",
  "finYear": "2026",
  "quarter": 3,
  "processingStatus": ""
}
```

### Get Ready Requests
```bash
GET /api/v1/tbr/ready-download-requests?tanId=PTLA13241E&page=0&size=10

Response:
{
  "requests": [...],
  "currentPage": 0,
  "pageSize": 10,
  "totalItems": 25,
  "totalPages": 3,
  "httpStatus": 200
}
```

### Initiate TBR
```bash
POST /api/v1/tbr/initiate?tan=PTLA13241E&finYear=2026&quarter=Q3

Response:
{
  "id": 1,
  "tan": "PTLA13241E",
  "financial_year": 2026,
  "quarter": "Q3",
  "request_id": "TBR_PTLA13241E_2026_Q3_a1b2c3d4",
  "status": "INITIATED",
  "processing_status": "Queued for processing",
  "total_records": 0,
  "processed_records": 0,
  "initiated_date": "2026-07-18T...",
  "completed_date": null
}
```

### Get Status
```bash
GET /api/v1/tbr/status/TBR_PTLA13241E_2026_Q3_a1b2c3d4

Response:
{
  "request_id": "TBR_PTLA13241E_2026_Q3_a1b2c3d4",
  "status": "PROCESSING",
  "processing_status": "Processing 75 of 150 records",
  "total_records": 150,
  "processed_records": 75,
  "initiated_date": "2026-07-15T10:30:00Z",
  "completed_date": null,
  "error_message": null
}
```

## Next Steps: Database Integration

The TBR service currently uses mock implementations. To connect to the database:

### 1. **Install Database Dependencies**
```bash
pip install sqlalchemy aiomysql
```

### 2. **Create TBR Models** (`tbr/core/models.py`)
```python
from sqlalchemy import Column, String, Integer, DateTime, Boolean
from database import Base

class TransactionBasedReport(Base):
    __tablename__ = "transaction_based_reports"
    id = Column(Integer, primary_key=True)
    tan = Column(String(10), index=True)
    financial_year = Column(Integer, index=True)
    quarter = Column(String(10), index=True)
    request_id = Column(String(100), unique=True)
    status = Column(String(50), index=True)
    # ... other fields
```

### 3. **Create Database Session** (`tbr/core/db.py`)
```python
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

DATABASE_URL = "mysql+aiomysql://user:pass@localhost/traces_db"
engine = create_async_engine(DATABASE_URL)
async_session = sessionmaker(engine, class_=AsyncSession)
```

### 4. **Update TBRService** (`tbr/services/tbr_service.py`)
Replace mock implementations with actual database queries:
```python
async def validate_tan(self, tan, financial_year, quarter):
    async with self.db_session() as session:
        stmt = select(TANStatement).where(
            (TANStatement.tan == tan) &
            (TANStatement.financial_year == financial_year) &
            (TANStatement.quarter == quarter)
        )
        result = await session.execute(stmt)
        statement = result.scalar_one_or_none()
        return {
            "isValid": statement is not None,
            ...
        }
```

## Configuration

### Environment Variables
```bash
# Database (when implemented)
DATABASE_URL=mysql+aiomysql://traces_user:pass@localhost/traces_db

# API
DEBUG=False
API_PREFIX=/api/v1
```

## Error Handling

The module includes comprehensive error handling:
- `HTTPException(400)` - Invalid input
- `HTTPException(404)` - Resource not found
- `HTTPException(500)` - Server error

All errors are logged to the application logger.

## Testing the TBR Module

### Start the API
```bash
docker-compose up
# or
python -m uvicorn api_service:app --reload
```

### Test Endpoints
```bash
# Test validate endpoint
curl -X POST http://localhost:8000/api/v1/tbr/validate-tan \
  -H "Content-Type: application/json" \
  -d '{"tan":"PTLA13241E","finYear":"2026","quarter":"3"}'

# Test list endpoint
curl http://localhost:8000/api/v1/tbr/ready-download-requests?tanId=PTLA13241E

# View API docs
http://localhost:8000/docs
```

## Architecture Pattern

The TBR module follows a layered architecture:

```
HTTP Request
    ↓
FastAPI Router (endpoints.py)
    ↓
Service Layer (tbr_service.py)
    ↓
Repository Layer (to be implemented)
    ↓
Database (MySQL)
```

**Benefits:**
- Clean separation of concerns
- Testable (mock service layer)
- Reusable (service can be used from CLI, async jobs, etc.)
- Follows existing project patterns

## Integration with Existing Features

The TBR module is independent of pan_verification and fetcher modules but can integrate with them:

1. **User context** - Can access authenticated user from pan_verification
2. **Job queue** - Can submit TBR generation jobs to the background worker queue
3. **Logging** - Uses same logging setup as other modules

## Future Enhancements

1. ✅ Database integration (pending MySQL setup)
2. ⏳ TBR file generation (Excel/PDF output)
3. ⏳ Async job processing (queue TBR generation in background)
4. ⏳ Caching layer (Redis for frequently accessed data)
5. ⏳ Rate limiting & quota management
6. ⏳ Audit logging


from fastapi import APIRouter, HTTPException
from conso.models.schemas import *
from conso.services.conso_service import ConsoServiceFactory
from conso.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

@router.get("/conso/quarters", response_model=GetQuartersResponse, tags=["CONSO File"])
async def get_quarters(financial_year: str):
    """Get available quarters for CONSO file"""
    try:
        service = ConsoServiceFactory.get_instance()
        quarters = await service.get_quarters(financial_year)
        return {"quarters": quarters}
    except Exception as e:
        logger.error(f"Error getting quarters: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/conso/initiate", response_model=InitiateConsoResponse, tags=["CONSO File"])
async def initiate_download(request: InitiateConsoRequest):
    """Initiate CONSO file download"""
    try:
        logger.info(f"POST /conso/initiate - {request.tan}")
        service = ConsoServiceFactory.get_instance()
        result = await service.initiate_download(
            request.tan, request.financial_year, request.quarter, request.form_type
        )
        return result
    except Exception as e:
        logger.error(f"Error initiating CONSO download: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/conso/status/{request_id}", response_model=CheckConsoStatusResponse, tags=["CONSO File"])
async def check_status(request_id: str):
    """Check status of CONSO file download"""
    try:
        service = ConsoServiceFactory.get_instance()
        status = await service.get_status(request_id)
        return status
    except Exception as e:
        logger.error(f"Error checking status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/conso/list", response_model=ListConsoResponse, tags=["CONSO File"])
async def list_requests(tan: str, page: int = 0, page_size: int = 10):
    """List CONSO requests for TAN"""
    try:
        logger.info(f"GET /conso/list - TAN: {tan}, page: {page}")
        service = ConsoServiceFactory.get_instance()
        result = await service.list_requests(tan, page, page_size)
        return result
    except Exception as e:
        logger.error(f"Error listing requests: {e}")
        raise HTTPException(status_code=500, detail=str(e))

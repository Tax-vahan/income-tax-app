from fastapi import APIRouter, HTTPException
from justification.models.schemas import *
from justification.services.justification_service import JustificationServiceFactory
from justification.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

@router.get("/justification/financial-years", response_model=GetFinYearResponse, tags=["Justification Report"])
async def get_financial_years():
    """Get available financial years for justification reports"""
    try:
        service = JustificationServiceFactory.get_instance()
        years = await service.get_financial_years()
        return {"financial_years": years}
    except Exception as e:
        logger.error(f"Error getting financial years: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/justification/form-types", response_model=GetFormTypeResponse, tags=["Justification Report"])
async def get_form_types(tan: str, financial_year: str):
    """Get available form types for TAN"""
    try:
        logger.info(f"GET /justification/form-types - TAN: {tan}, FY: {financial_year}")
        service = JustificationServiceFactory.get_instance()
        form_types = await service.get_form_types(tan, financial_year)
        return {"form_types": form_types}
    except Exception as e:
        logger.error(f"Error getting form types: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/justification/initiate", response_model=InitiateJustificationResponse, tags=["Justification Report"])
async def initiate_report(request: InitiateJustificationRequest):
    """Initiate justification report download"""
    try:
        logger.info(f"POST /justification/initiate - {request.tan}")
        service = JustificationServiceFactory.get_instance()
        result = await service.initiate_report(request.tan, request.financial_year, request.form_type)
        return result
    except Exception as e:
        logger.error(f"Error initiating report: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/justification/status/{request_id}", response_model=CheckJustificationStatusResponse, tags=["Justification Report"])
async def check_status(request_id: str):
    """Check status of justification report"""
    try:
        service = JustificationServiceFactory.get_instance()
        status = await service.get_status(request_id)
        return status
    except Exception as e:
        logger.error(f"Error checking status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/justification/list", response_model=ListJustificationResponse, tags=["Justification Report"])
async def list_reports(tan: str, page: int = 0, page_size: int = 10):
    """List justification reports for TAN"""
    try:
        service = JustificationServiceFactory.get_instance()
        result = await service.list_reports(tan, page, page_size)
        return result
    except Exception as e:
        logger.error(f"Error listing reports: {e}")
        raise HTTPException(status_code=500, detail=str(e))

from fastapi import APIRouter, HTTPException
from tdstcs.models.schemas import (
    InitiateTDSTCSRequest,
    InitiateTDSTCSResponse,
    CheckTDSTCSStatusRequest,
    CheckTDSTCSStatusResponse,
    GetFormTypesResponse,
    GetQuartersResponse,
    ListTDSTCSResponse,
    ErrorResponse,
)
from tdstcs.services.tdstcs_service import TDSTCSServiceFactory
from tdstcs.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/tdstcs/form-types", response_model=GetFormTypesResponse, tags=["TDS/TCS Certificates"])
async def get_form_types():
    """Get available TDS/TCS form types (131, 133)"""
    try:
        service = TDSTCSServiceFactory.get_instance()
        form_types = await service.get_form_types()
        return {"form_types": form_types, "message": "Form types retrieved successfully"}
    except Exception as e:
        logger.error(f"Error getting form types: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tdstcs/quarters", response_model=GetQuartersResponse, tags=["TDS/TCS Certificates"])
async def get_quarters(financial_year: str):
    """Get available quarters for a financial year"""
    try:
        service = TDSTCSServiceFactory.get_instance()
        quarters = await service.get_quarters(financial_year)
        return {"quarters": quarters, "message": "Quarters retrieved successfully"}
    except Exception as e:
        logger.error(f"Error getting quarters: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/tdstcs/initiate", response_model=InitiateTDSTCSResponse, tags=["TDS/TCS Certificates"]
)
async def initiate_certificate_download(request: InitiateTDSTCSRequest):
    """
    Initiate a TDS/TCS certificate download request.

    **Parameters:**
    - tan: Tax Account Number
    - financial_year: Financial year (e.g., 2026-27)
    - quarter: Quarter (Q1, Q2, Q3, Q4)
    - form_type: Form type (131, 133)

    **Returns:** request_id to track the download
    """
    try:
        logger.info(
            f"POST /tdstcs/initiate - {request.tan} ({request.form_type}, {request.financial_year}, {request.quarter})"
        )

        service = TDSTCSServiceFactory.get_instance()
        result = await service.initiate_certificate_download(
            tan=request.tan,
            financial_year=request.financial_year,
            quarter=request.quarter,
            form_type=request.form_type,
        )
        return result
    except Exception as e:
        logger.error(f"Error initiating certificate download: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/tdstcs/status/{request_id}",
    response_model=CheckTDSTCSStatusResponse,
    tags=["TDS/TCS Certificates"],
)
async def check_status(request_id: str):
    """Check status of a TDS/TCS certificate download request"""
    try:
        logger.debug(f"GET /tdstcs/status/{request_id}")

        service = TDSTCSServiceFactory.get_instance()
        status = await service.get_status(request_id)
        return status
    except Exception as e:
        logger.error(f"Error checking certificate status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tdstcs/list", response_model=ListTDSTCSResponse, tags=["TDS/TCS Certificates"])
async def list_certificates(tan: str, page: int = 0, page_size: int = 10):
    """List completed TDS/TCS certificate downloads for a TAN"""
    try:
        logger.info(f"GET /tdstcs/list - TAN: {tan}, page: {page}")

        service = TDSTCSServiceFactory.get_instance()
        result = await service.list_completed_certificates(
            tan=tan, page=page, page_size=page_size
        )
        return result
    except Exception as e:
        logger.error(f"Error listing certificates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

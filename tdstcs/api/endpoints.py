import io
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
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
async def get_form_types(session_id: str = Query(..., description="Active session ID from /pan/login/complete")):
    """Get available TDS/TCS form types (130, 131, 133) live from TRACES."""
    try:
        service = TDSTCSServiceFactory.get_instance()
        form_types = await service.get_form_types(session_id)
        return {"form_types": form_types, "message": "Form types retrieved successfully"}
    except Exception as e:
        logger.error(f"Error getting form types: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tdstcs/quarters", response_model=GetQuartersResponse, tags=["TDS/TCS Certificates"])
async def get_quarters(
    financial_year: str,
    session_id: str = Query(..., description="Active session ID from /pan/login/complete"),
):
    """Get available quarters for a financial year, live from TRACES."""
    try:
        service = TDSTCSServiceFactory.get_instance()
        quarters = await service.get_quarters(session_id, financial_year)
        return {"quarters": quarters, "message": "Quarters retrieved successfully"}
    except Exception as e:
        logger.error(f"Error getting quarters: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/tdstcs/initiate", response_model=InitiateTDSTCSResponse, tags=["TDS/TCS Certificates"]
)
async def initiate_certificate_download(request: InitiateTDSTCSRequest):
    """
    Initiate a TDS/TCS certificate download request against the real TRACES
    API ("All PANs" option only — see tdstcs_automation.py for why PAN /
    Bulk PAN Upload aren't wired up yet).

    **Parameters:**
    - session_id: Active session from /pan/login/complete
    - tan: Tax Account Number
    - financial_year: Financial year (e.g., 2026-27)
    - quarter: Quarter (Q1, Q2, Q3, Q4)
    - form_type: Form type (130, 131, 133)

    **Returns:** request_id to track the download
    """
    try:
        logger.info(
            f"POST /tdstcs/initiate - {request.tan} ({request.form_type}, {request.financial_year}, {request.quarter})"
        )

        service = TDSTCSServiceFactory.get_instance()
        result = await service.initiate_certificate_download(
            session_id=request.session_id,
            tan=request.tan,
            financial_year=request.financial_year,
            quarter=request.quarter,
            form_type=request.form_type,
        )
        return result
    except ValueError as e:
        error_msg = str(e)
        logger.warning(f"Invalid request for TDS/TCS initiate: {error_msg}")
        raise HTTPException(status_code=401, detail=error_msg)
    except Exception as e:
        logger.error(f"Error initiating certificate download: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Certificate download initiation failed. Please try again later.")


@router.get(
    "/tdstcs/status/{request_id}",
    response_model=CheckTDSTCSStatusResponse,
    tags=["TDS/TCS Certificates"],
)
async def check_status(
    request_id: str,
    session_id: str = Query(..., description="Active session ID from /pan/login/complete"),
):
    """Check status of a TDS/TCS certificate download request, refreshed live from TRACES."""
    try:
        logger.debug(f"GET /tdstcs/status/{request_id}")

        service = TDSTCSServiceFactory.get_instance()
        status = await service.get_status(session_id, request_id)
        return status
    except Exception as e:
        logger.error(f"Error checking certificate status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tdstcs/download/{request_id}", tags=["TDS/TCS Certificates"])
async def download_certificate(
    request_id: str,
    session_id: str = Query(..., description="Active session ID from /pan/login/complete"),
):
    """
    Download the generated TDS/TCS certificate file. Call /tdstcs/status
    first and only download once is_ready=true.

    NOTE: the underlying TRACES download endpoint is unverified (best-effort
    implementation — see tdstcs_automation.py's download() docstring). If
    this 404s or returns an unexpected shape once a real ready request is
    available, that's the endpoint/response format to fix, not this route.
    """
    try:
        service = TDSTCSServiceFactory.get_instance()
        file_bytes, filename = await service.download_certificate(session_id, request_id)
        return StreamingResponse(
            io.BytesIO(file_bytes),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error downloading certificate: {e}", exc_info=True)
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

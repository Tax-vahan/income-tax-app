from fastapi import APIRouter, Depends, Query, HTTPException, status
from typing import Optional
from datetime import datetime

from tbr.models.schemas import (
    TBRValidationRequest,
    TANValidationResponse,
    PaginatedTBRResponse,
    TransactionBasedReportDTO,
    InitiateTBRRequest,
    TBRStatusResponse,
    FinYearDTO,
    QuarterDTO,
    ErrorResponse,
)
from typing import List
from tbr.services.tbr_service import TBRService, get_tbr_service
from tbr.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/tbr")


@router.get(
    "/financial-years",
    response_model=List[FinYearDTO],
    summary="Get available financial years for TBR",
)
async def get_financial_years(
    session_id: str = Query(..., description="Active session ID from /pan/login/complete"),
    service: TBRService = Depends(get_tbr_service),
):
    try:
        return await service.get_financial_years(session_id)
    except Exception as e:
        logger.error(f"Error fetching TBR financial years: {e}")
        raise HTTPException(status_code=500, detail="Error fetching financial years")


@router.get(
    "/quarters",
    response_model=List[QuarterDTO],
    summary="Get available quarters for TBR",
)
async def get_quarters(
    session_id: str = Query(..., description="Active session ID from /pan/login/complete"),
    financial_year: str = Query(..., description="Financial year, e.g. 2026"),
    service: TBRService = Depends(get_tbr_service),
):
    try:
        return await service.get_quarters(session_id, financial_year)
    except Exception as e:
        logger.error(f"Error fetching TBR quarters: {e}")
        raise HTTPException(status_code=500, detail="Error fetching quarters")


@router.post(
    "/validate-tan",
    response_model=TANValidationResponse,
    summary="Validate TAN for TBR generation"
)
async def validate_tan(
    request: TBRValidationRequest,
    service: TBRService = Depends(get_tbr_service),
):
    """
    Validate if TAN statement is available for given financial year and quarter.

    Returns whether a statement is available for TBR generation.
    """
    try:
        fin_year = int(request.finYear)
        result = await service.validate_tan(
            request.session_id,
            request.tan,
            fin_year,
            request.quarter
        )
        return TANValidationResponse(**result)
    except ValueError as e:
        logger.error(f"Invalid financial year: {request.finYear}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid financial year"
        )
    except Exception as e:
        logger.error(f"Error validating TAN: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error validating TAN"
        )


@router.get(
    "/ready-download-requests",
    response_model=PaginatedTBRResponse,
    summary="Get ready-to-download TBR requests"
)
async def get_ready_requests(
    session_id: str = Query(..., description="Active session ID from /pan/login/complete"),
    tanId: str = Query(..., description="Tax Account Number"),
    page: int = Query(0, ge=0, description="Page number (0-indexed)"),
    size: int = Query(10, ge=1, le=100, description="Page size"),
    service: TBRService = Depends(get_tbr_service),
):
    """
    Get list of TBR requests that are ready for download.

    Returns paginated list of our own locally-tracked completed TBR
    requests, plus the raw live TRACES ready-download-requests payload
    under `traces_live_data` for cross-checking.
    """
    try:
        result = await service.get_ready_requests(session_id, tanId, page, size)
        return PaginatedTBRResponse(**result)
    except Exception as e:
        logger.error(f"Error fetching ready requests: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching ready requests"
        )


@router.post(
    "/initiate",
    response_model=TransactionBasedReportDTO,
    summary="Initiate new TBR request"
)
async def initiate_tbr(
    request: InitiateTBRRequest,
    service: TBRService = Depends(get_tbr_service),
):
    """
    Initiate a new TBR (Transaction Based Report) generation request.

    Validates the TAN against TRACES first (a false isValid — "No statement
    is available for the selected period" — comes back as status FAILED,
    not an HTTP error), then calls TRACES's report-generation endpoint.
    """
    try:
        result = await service.initiate_tbr_request(
            request.session_id, request.tan, request.financial_year, request.quarter, "system"
        )
        return TransactionBasedReportDTO(**result)
    except ValueError as e:
        error_msg = str(e)
        logger.warning(f"Invalid request for TBR initiate: {error_msg}")
        raise HTTPException(status_code=401, detail=error_msg)
    except Exception as e:
        logger.error(f"Error initiating TBR: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="TBR request initiation failed. Please try again later."
        )


@router.get(
    "/status/{request_id}",
    response_model=Optional[TBRStatusResponse],
    summary="Get TBR request status"
)
async def get_status(
    request_id: str,
    service: TBRService = Depends(get_tbr_service),
):
    """
    Get status of a TBR request.
    
    Returns current processing status, progress, and completion details.
    """
    try:
        result = await service.get_status(request_id)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Request not found"
            )
        return TBRStatusResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching status"
        )

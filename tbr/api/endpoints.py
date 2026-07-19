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
    ErrorResponse,
)
from tbr.services.tbr_service import TBRService, get_tbr_service
from tbr.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/tbr", tags=["Transaction Based Report"])


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
    tanId: str = Query(..., description="Tax Account Number"),
    page: int = Query(0, ge=0, description="Page number (0-indexed)"),
    size: int = Query(10, ge=1, le=100, description="Page size"),
    service: TBRService = Depends(get_tbr_service),
):
    """
    Get list of TBR requests that are ready for download.
    
    Returns paginated list of completed TBR reports.
    """
    try:
        result = await service.get_ready_requests(tanId, page, size)
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
    tan: str = Query(..., description="Tax Account Number"),
    finYear: int = Query(..., description="Financial Year"),
    quarter: str = Query(..., description="Quarter"),
    service: TBRService = Depends(get_tbr_service),
):
    """
    Initiate a new TBR (Transaction Based Report) generation request.
    
    Creates a new request and queues it for processing.
    """
    try:
        result = await service.initiate_tbr_request(tan, finYear, quarter, "system")
        return TransactionBasedReportDTO(**result)
    except Exception as e:
        logger.error(f"Error initiating TBR: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error initiating TBR request"
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

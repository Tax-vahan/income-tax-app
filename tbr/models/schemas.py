from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class TBRStatusEnum(str, Enum):
    PENDING = "PENDING"
    INITIATED = "INITIATED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class QuarterDTO(BaseModel):
    code: str
    description: str
    displayName: str


class TBRValidationRequest(BaseModel):
    tan: str = Field(..., description="Tax Account Number")
    finYear: str = Field(..., description="Financial Year")
    quarter: str = Field(..., description="Quarter (Q1-Q4)")


class TANValidationResponse(BaseModel):
    isValid: bool
    message: str
    tan: str
    finYear: str
    quarter: int
    processingStatus: str = ""


class TransactionBasedReportDTO(BaseModel):
    id: int
    tan: str
    financial_year: int
    quarter: str
    request_id: str
    status: str
    processing_status: str
    total_records: int
    processed_records: int
    initiated_date: Optional[datetime] = None
    completed_date: Optional[datetime] = None

    class Config:
        from_attributes = True


class PaginatedTBRResponse(BaseModel):
    requests: List[TransactionBasedReportDTO]
    currentPage: int
    pageSize: int
    totalItems: int
    totalPages: int
    httpStatus: int = 200


class InitiateTBRRequest(BaseModel):
    tan: str = Field(..., description="Tax Account Number")
    financial_year: int = Field(..., description="Financial Year")
    quarter: str = Field(..., description="Quarter")


class TBRStatusResponse(BaseModel):
    request_id: str
    status: str
    processing_status: str
    total_records: int
    processed_records: int
    initiated_date: Optional[datetime] = None
    completed_date: Optional[datetime] = None
    error_message: Optional[str] = None


class ErrorResponse(BaseModel):
    error: str
    message: str
    timestamp: datetime

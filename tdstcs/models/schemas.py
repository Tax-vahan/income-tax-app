from pydantic import BaseModel, Field
from typing import Optional, List


class FormType(BaseModel):
    code: str = Field(..., description="Form type code (130, 131, 133)")
    description: str = Field(..., description="Form type description")


class Quarter(BaseModel):
    code: str = Field(..., description="Quarter code (1, 2, 3, 4)")
    description: str = Field(..., description="Quarter display name (Q1, Q2, etc)")
    displayName: str = Field(..., description="Display name for UI")


class GetFormTypesResponse(BaseModel):
    form_types: List[FormType] = Field(..., description="Available form types")
    message: str = "Form types retrieved successfully"


class GetQuartersResponse(BaseModel):
    quarters: List[Quarter] = Field(..., description="Available quarters for financial year")
    message: str = "Quarters retrieved successfully"


class InitiateTDSTCSRequest(BaseModel):
    tan: str = Field(..., description="Tax Account Number")
    financial_year: str = Field(..., description="Financial year (e.g., 2026-27)")
    quarter: str = Field(..., description="Quarter (Q1, Q2, Q3, Q4)")
    form_type: str = Field(..., description="Form type code (131, 133)")


class InitiateTDSTCSResponse(BaseModel):
    request_id: str = Field(..., description="Request ID for tracking")
    status: str = Field(..., description="Status (INITIATED, PROCESSING, COMPLETED, FAILED)")
    message: str = "Certificate download initiated"
    transaction_id: Optional[str] = None


class CheckTDSTCSStatusRequest(BaseModel):
    request_id: str = Field(..., description="Request ID from initiate response")


class CheckTDSTCSStatusResponse(BaseModel):
    request_id: str = Field(..., description="Request ID")
    status: str = Field(..., description="Status (INITIATED, PROCESSING, COMPLETED, FAILED)")
    is_ready: bool = Field(..., description="Whether file is ready to download")
    message: str = "Certificate status retrieved"
    progress: int = Field(default=0, description="Progress percentage (0-100)")
    file_path: Optional[str] = None


class ListTDSTCSResponse(BaseModel):
    certificates: List[dict] = Field(..., description="List of available certificates")
    total: int = Field(..., description="Total count")
    page: int = Field(..., description="Current page")
    message: str = "Certificates retrieved successfully"


class ErrorResponse(BaseModel):
    error: str = Field(..., description="Error message")
    detail: Optional[str] = None

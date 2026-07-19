from pydantic import BaseModel, Field
from typing import Optional, List

class FinancialYear(BaseModel):
    code: str = Field(..., description="Year code (2026)")
    description: str = Field(..., description="Year description (2026-27)")
    yearValue: int = Field(..., description="Year value")

class FormType(BaseModel):
    code: str = Field(..., description="Form type code")
    description: str = Field(..., description="Form type description")

class GetFinYearResponse(BaseModel):
    financial_years: List[FinancialYear] = Field(..., description="Available financial years")
    message: str = "Financial years retrieved successfully"

class GetFormTypeResponse(BaseModel):
    form_types: List[FormType] = Field(..., description="Available form types")
    message: str = "Form types retrieved successfully"

class InitiateJustificationRequest(BaseModel):
    tan: str = Field(..., description="Tax Account Number")
    financial_year: str = Field(..., description="Financial year (e.g., 2026)")
    form_type: str = Field(..., description="Form type code")

class InitiateJustificationResponse(BaseModel):
    request_id: str = Field(..., description="Request ID for tracking")
    status: str = Field(..., description="Status (INITIATED, PROCESSING, COMPLETED, FAILED)")
    message: str = "Justification report initiated"
    transaction_id: Optional[str] = None

class CheckJustificationStatusResponse(BaseModel):
    request_id: str = Field(..., description="Request ID")
    status: str = Field(..., description="Status")
    is_ready: bool = Field(..., description="Whether report is ready")
    message: str = "Report status retrieved"
    progress: int = Field(default=0, description="Progress percentage (0-100)")

class ListJustificationResponse(BaseModel):
    reports: List[dict] = Field(..., description="List of reports")
    total: int = Field(..., description="Total count")
    page: int = Field(..., description="Current page")
    message: str = "Reports retrieved successfully"

class ErrorResponse(BaseModel):
    error: str = Field(..., description="Error message")
    detail: Optional[str] = None

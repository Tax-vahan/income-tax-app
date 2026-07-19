from pydantic import BaseModel, Field
from typing import Optional, List

class Quarter(BaseModel):
    code: str = Field(..., description="Quarter code")
    description: str = Field(..., description="Quarter description")
    displayName: str = Field(..., description="Display name")

class GetQuartersResponse(BaseModel):
    quarters: List[Quarter] = Field(..., description="Available quarters")
    message: str = "Quarters retrieved successfully"

class InitiateConsoRequest(BaseModel):
    tan: str = Field(..., description="Tax Account Number")
    financial_year: str = Field(..., description="Financial year (e.g., 2026)")
    quarter: str = Field(..., description="Quarter (Q1, Q2, Q3, Q4)")
    form_type: str = Field(..., description="Form type code")

class InitiateConsoResponse(BaseModel):
    request_id: str = Field(..., description="Request ID for tracking")
    status: str = Field(..., description="Status")
    message: str = "CONSO file download initiated"
    request_ids: Optional[List[str]] = None

class CheckConsoStatusResponse(BaseModel):
    request_id: str = Field(..., description="Request ID")
    status: str = Field(..., description="Status")
    is_ready: bool = Field(..., description="Whether file is ready")
    message: str = "Status retrieved"
    progress: int = Field(default=0, description="Progress percentage")

class ConsoRequest(BaseModel):
    request_id: str
    tan: str
    financial_year: str
    quarter: str
    form_type: str
    status: str
    initiated_date: str
    completed_date: Optional[str] = None

class ListConsoResponse(BaseModel):
    requests: List[ConsoRequest] = Field(..., description="List of CONSO requests")
    current_page: int = Field(..., description="Current page")
    total_pages: int = Field(..., description="Total pages")
    total_elements: int = Field(..., description="Total elements")
    page_size: int = Field(..., description="Page size")
    has_next: bool = Field(..., description="Has next page")
    has_previous: bool = Field(..., description="Has previous page")
    message: str = "CONSO requests retrieved"

class ErrorResponse(BaseModel):
    error: str = Field(..., description="Error message")
    detail: Optional[str] = None

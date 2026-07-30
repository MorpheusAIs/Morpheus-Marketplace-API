"""
Pydantic schemas for Coinbase Business Payment Link API.
"""
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class CreatePaymentLinkRequest(BaseModel):
    """Request to create a new Coinbase Payment Link."""
    amount: str = Field(..., description="Payment amount (e.g., '100.00')")
    currency: str = Field(default="USDC", description="Currency code (currently only USDC)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Key-value pairs passed through the payment flow")
    description: Optional[str] = Field(None, description="Description shown on the payment page")
    success_redirect_url: Optional[str] = Field(None, description="HTTPS URL to redirect on success")
    failure_redirect_url: Optional[str] = Field(None, description="HTTPS URL to redirect on failure")
    expires_at: Optional[str] = Field(None, description="ISO 8601 timestamp when the link expires")


class PaymentLinkResponse(BaseModel):
    """Response from the Coinbase Payment Link API."""
    id: str = Field(..., description="Payment link ID (24-char hex)")
    url: Optional[str] = Field(None, description="Payment link URL")
    status: Optional[str] = Field(None, description="ACTIVE, COMPLETED, EXPIRED, or DEACTIVATED")
    amount: Optional[str] = None
    currency: Optional[str] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = Field(None, alias="createdAt")
    updated_at: Optional[str] = Field(None, alias="updatedAt")
    expires_at: Optional[str] = Field(None, alias="expiresAt")

    model_config = {"populate_by_name": True}


class PaymentLinkListResponse(BaseModel):
    """Paginated list of payment links."""
    payment_links: List[Dict[str, Any]] = Field(default_factory=list, alias="paymentLinks")
    next_page_token: Optional[str] = Field(None, alias="nextPageToken")

    model_config = {"populate_by_name": True}

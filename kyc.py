"""
Schemas for Phase 3 — KYC submission and admin review.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class KYCSubmissionOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    document_type: str
    document_number: str
    status: str
    rejection_reason: Optional[str]
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    has_back_document: bool = False
    has_selfie: bool = False
    user_email: Optional[str] = None
    user_full_name: Optional[str] = None

    model_config = {"from_attributes": True}


class KYCReviewRequest(BaseModel):
    approve: bool
    rejection_reason: Optional[str] = Field(
        default=None,
        description="Required when approve=false",
        max_length=500,
    )

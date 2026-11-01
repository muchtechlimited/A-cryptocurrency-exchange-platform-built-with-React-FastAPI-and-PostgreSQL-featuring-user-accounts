"""
Schemas for the support ticket system.
"""

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

TicketSubject = Literal[
    "Deposit issue", "Withdrawal issue", "Buy / Sell issue",
    "Account & verification", "Something else",
]


class TicketCreate(BaseModel):
    subject: TicketSubject
    message: str = Field(min_length=1, max_length=2000)
    reference: Optional[str] = Field(default=None, max_length=200)


class TicketOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    subject: str
    message: str
    reference: Optional[str]
    status: str
    admin_response: Optional[str]
    responded_by: Optional[uuid.UUID]
    responded_at: Optional[datetime]
    created_at: datetime
    # Populated only on the admin listing (see _to_out in the router) so an
    # admin reviewing the queue can see who each ticket is from without a
    # second lookup — mirrors the same pattern KYCSubmissionOut uses.
    user_email: Optional[str] = None
    user_full_name: Optional[str] = None

    model_config = {"from_attributes": True}


class TicketRespondRequest(BaseModel):
    response: str = Field(min_length=1, max_length=2000)
    status: Literal["pending", "resolved"] = "resolved"

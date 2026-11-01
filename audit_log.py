"""
Schema for the general admin audit log (see app/models/audit_log.py).
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AdminAuditLogOut(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID
    action: str
    target_type: str
    target_id: Optional[str]
    target_user_id: Optional[uuid.UUID]
    summary: str
    details: Optional[str]
    created_at: datetime
    ip_address: Optional[str] = None
    device_label: Optional[str] = None

    # Populated by _to_out() in the router — an admin reviewing the log
    # needs to see who did it and, when applicable, who it was done to,
    # without a second lookup. Same enrichment pattern as
    # KYCSubmissionOut.user_email / TicketOut.user_email.
    actor_email: Optional[str] = None
    actor_full_name: Optional[str] = None
    target_user_email: Optional[str] = None
    target_user_full_name: Optional[str] = None

    model_config = {"from_attributes": True}

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ip_address: Optional[str] = None
    device_label: Optional[str] = None
    created_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None

    # Computed by the router, not read straight off the model:
    is_current: bool = False
    is_active: bool = False


class RevokeSessionsResponse(BaseModel):
    message: str
    revoked_count: int

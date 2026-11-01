import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: uuid.UUID
    type: str
    title: str
    message: str
    related_id: Optional[uuid.UUID]
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}

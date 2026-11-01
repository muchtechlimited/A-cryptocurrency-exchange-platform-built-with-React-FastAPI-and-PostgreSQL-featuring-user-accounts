"""
Support ticket model.

Users submit a ticket from the Support page with a subject, message,
and an optional transaction reference. Admins respond from the admin
panel. Uses its own three-state lifecycle (open -> pending -> resolved)
rather than reusing pending/approved/rejected — "approved/rejected"
doesn't make sense for a support conversation the way it does for a
deposit or withdrawal request.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class TicketStatus:
    OPEN = "open"
    PENDING = "pending"
    RESOLVED = "resolved"
    ALL = [OPEN, PENDING, RESOLVED]


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    subject = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    reference = Column(String, nullable=True)  # optional transaction reference the user provides

    status = Column(String, default=TicketStatus.OPEN, nullable=False)

    admin_response = Column(Text, nullable=True)
    responded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

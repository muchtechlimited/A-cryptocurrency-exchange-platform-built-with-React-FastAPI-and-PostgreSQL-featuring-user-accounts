"""
Login/session tracking.

Roadmap Priority 4 ("User Activity & Security"): login history, active
devices, IP addresses. Every successful login (via /auth/register or
/auth/login) creates one UserSession row, which serves double duty:

  - "Login history": every row ever created for this user, newest first.
  - "Active devices": rows that are neither revoked nor past their
    expiry — i.e. a token for that session could still be accepted.

The access token issued at login carries this row's `jti` as a claim
(see app/core/security.py). Every authenticated request looks the jti
up here (see app/core/deps.py) so that revoking a session — "sign out
this device" — takes effect immediately, not just once the JWT expires
on its own. This is the same tradeoff as any session-table-backed JWT:
one extra indexed lookup per request, in exchange for actual revocation
instead of just a login list nobody can act on.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    # Matches the "jti" claim of the access token issued for this login.
    # Looked up on every authenticated request, so it's indexed and unique.
    jti = Column(String, nullable=False, unique=True, index=True)

    # "user" or "admin" — mirrors the token's own scope claim, so an
    # admin's sessions and their occasional customer-facing sessions (if
    # any) are never confused with each other.
    scope = Column(String, nullable=False)

    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    # Short human-readable label derived from user_agent at login time,
    # e.g. "Chrome on Windows" — see app/utils/device.py. Stored rather
    # than re-derived on every read so a future parser change doesn't
    # rewrite history under people's feet.
    device_label = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)
    # Mirrors the access token's own "exp" claim, so "is this session
    # still active" can be answered without decoding any token.
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # Set when the user (or an admin, in a future extension) explicitly
    # signs this device out. Null means "never revoked" — still doesn't
    # mean "active", since it may simply have expired; see expires_at.
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class LoginAttempt(Base):
    """
    One row per sign-in attempt, successful or not (password step and 2FA step).

    UserSession only ever records successes, so it can't answer "who has been
    trying to get into the admin accounts?". This table can. `user_id` is NULL
    when the email matched no account; the raw email is kept so repeated
    guessing at one address is still visible.
    """
    __tablename__ = "login_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    success = Column(Boolean, nullable=False, default=False, index=True)
    # bad_credentials | account_disabled | no_role | bad_2fa_code | 2fa_locked | (empty on success)
    reason = Column(String, nullable=True)
    stage = Column(String, nullable=False, default="password")  # password | 2fa
    ip_address = Column(String, nullable=True, index=True)
    user_agent = Column(String, nullable=True)
    device_label = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)

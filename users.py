"""
User profile and settings routes (all require a logged-in user):
  GET  /users/me/profile        - view profile
  PUT  /users/me/profile        - update profile fields
  POST /users/me/avatar         - upload/replace avatar image
  GET  /users/me/settings       - view settings
  PUT  /users/me/settings       - update settings
  POST /users/me/change-password
"""

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_session, get_current_user, get_current_user_or_admin
from app.core.rate_limit import limiter
from app.models.login_event import UserSession
from app.utils.session_service import revoke_user_sessions
from app.core.security import hash_password, verify_password
from app.models.user import User
from app.schemas.profile import (
    ChangePasswordRequest,
    ProfileOut,
    ProfileUpdate,
    SettingsOut,
    SettingsUpdate,
)
from app.schemas.user import MessageResponse
from app.utils.file_upload import save_upload_file

router = APIRouter()


@router.get("/me/profile", response_model=ProfileOut)
def get_profile(current_user: User = Depends(get_current_user_or_admin)):
    return ProfileOut.model_validate(current_user)


@router.put("/me/profile", response_model=ProfileOut)
def update_profile(
    payload: ProfileUpdate,
    current_user: User = Depends(get_current_user_or_admin),
    db: Session = Depends(get_db),
):
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(current_user, field, value)

    db.commit()
    db.refresh(current_user)
    return ProfileOut.model_validate(current_user)


@router.post("/me/avatar", response_model=ProfileOut)
def upload_avatar(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Avatars are non-sensitive, so they use the public (statically served) upload dir.
    avatar_url = save_upload_file(file, subfolder="avatars", private=False)
    current_user.avatar_url = avatar_url

    db.commit()
    db.refresh(current_user)
    return ProfileOut.model_validate(current_user)


@router.get("/me/settings", response_model=SettingsOut)
def get_settings_endpoint(current_user: User = Depends(get_current_user)):
    return SettingsOut.model_validate(current_user)


@router.put("/me/settings", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(current_user, field, value)

    db.commit()
    db.refresh(current_user)
    return SettingsOut.model_validate(current_user)


@router.post("/me/change-password", response_model=MessageResponse)
@limiter.limit("5/minute")
def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user_or_admin),
    current_session: UserSession = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

    current_user.hashed_password = hash_password(payload.new_password)
    # Keep this device signed in, sign out every other one.
    revoke_user_sessions(db, current_user.id, except_session_id=current_session.id)
    db.commit()

    return MessageResponse(message="Password changed successfully")

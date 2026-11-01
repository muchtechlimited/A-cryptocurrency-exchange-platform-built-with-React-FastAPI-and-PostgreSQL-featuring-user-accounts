"""
Health check endpoints — used to confirm the API is running and that
the database connection is actually working (not just that FastAPI booted).
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter(tags=["Health"])


@router.get("/health")
def health_check():
    """Basic liveness check — does not touch the database."""
    return {"status": "ok"}


@router.get("/health/db")
def health_check_db(db: Session = Depends(get_db)):
    """Readiness check — confirms the app can actually reach PostgreSQL."""
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}

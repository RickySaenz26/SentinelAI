from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.core.config import settings
from app.core.errors import ApplicationError
from app.platform.database.health import is_database_ready

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "sentinelai-backend"
    version: str


def _health_response() -> HealthResponse:
    return HealthResponse(version=settings.app_version)


@router.get("/health", response_model=HealthResponse, status_code=status.HTTP_200_OK)
def health_check() -> HealthResponse:
    """Compatibility endpoint for the initial frontend/backend scaffold."""
    return _health_response()


@router.get("/health/live", response_model=HealthResponse, status_code=status.HTTP_200_OK)
def liveness_check() -> HealthResponse:
    """Report whether the API process is alive."""
    return _health_response()


@router.get("/health/ready", response_model=HealthResponse, status_code=status.HTTP_200_OK)
def readiness_check() -> HealthResponse:
    """Report whether the configured PostgreSQL runtime dependency is ready."""
    if settings.database_url_value is not None and not is_database_ready():
        raise ApplicationError("DEPENDENCY_UNAVAILABLE", "Dependencia no disponible.", 503)
    return _health_response()

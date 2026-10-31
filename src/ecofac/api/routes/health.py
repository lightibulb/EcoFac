# Copyright 2026 Lightibulb

"""Health check endpoint."""

from fastapi import APIRouter
from ecofac.api.schemas import HealthResponse

router = APIRouter()

@router.get("/health", response_model=HealthResponse, tags=["Health"])
def health_check() -> HealthResponse:
    """Basic health check."""
    return HealthResponse(status="ok")

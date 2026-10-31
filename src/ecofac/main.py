# Copyright 2026 Lightibulb

"""FastAPI application entry point."""

from fastapi import FastAPI

from ecofac.api.routes import audit, entities, health, imports, incidents, intelligence

app = FastAPI(
    title="EcoFac API",
    version="0.1.0",
    description="Local demonstration API. Not production-secure. Requires auth for public deployment.",
)

app.include_router(health.router)
app.include_router(imports.router, prefix="/api/v1")
app.include_router(entities.router, prefix="/api/v1")
app.include_router(intelligence.router, prefix="/api/v1")
app.include_router(incidents.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")

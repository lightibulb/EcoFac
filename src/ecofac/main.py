# Copyright 2026 Lightibulb

"""FastAPI application entry point."""

import pathlib

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ecofac.api.routes import (
    audit,
    dashboard,
    entities,
    health,
    imports,
    incidents,
    intelligence,
)

BASE_DIR = pathlib.Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="EcoFac API",
    version="0.1.0",
    description=(
        "Local demonstration API. Not production-secure. "
        "Requires auth for public deployment."
    ),
)

STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# API Routers
app.include_router(health.router)
app.include_router(imports.router, prefix="/api/v1")
app.include_router(entities.router, prefix="/api/v1")
app.include_router(intelligence.router, prefix="/api/v1")
app.include_router(incidents.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")


@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
def serve_dashboard(request: Request) -> HTMLResponse:
    """Serve the main dashboard UI."""
    return templates.TemplateResponse(request=request, name="index.html")

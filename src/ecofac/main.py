# Copyright 2026 Lightibulb

"""FastAPI application entry point."""

import math
import os
import pathlib
import sqlite3
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ecofac.api.middleware import (
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)
from ecofac.api.rate_limit import RateLimitMiddleware
from ecofac.api.routes import (
    audit,
    dashboard,
    decisions,
    entities,
    health,
    imports,
    incidents,
    intelligence,
    scenarios,
    world,
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

# Middleware stack, outermost first on the request path:
#   SecurityHeaders (outermost) -> RequestID -> RateLimit (innermost)
# Starlette adds middleware at the FRONT of the stack, so the LAST
# add_middleware call is the OUTERMOST. RateLimit's 429 short-circuit
# still flows back through RequestID and SecurityHeaders on the
# response path, so every 429 carries X-Request-ID and the standard
# security headers.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

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
app.include_router(decisions.router, prefix="/api/v1")
app.include_router(scenarios.router, prefix="/api/v1")
app.include_router(world.router, prefix="/api/v1")


@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
def serve_dashboard(request: Request) -> HTMLResponse:
    """Serve the main dashboard UI."""
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/decisions", response_class=HTMLResponse, tags=["Decision Center"])
def serve_decisions(request: Request) -> HTMLResponse:
    """Serve the Decision Center UI."""
    return templates.TemplateResponse(request=request, name="decisions.html")

@app.get("/scenarios", response_class=HTMLResponse, tags=["Scenario Workbench"])
def serve_scenarios(request: Request) -> HTMLResponse:
    """Serve the Scenario Workbench UI."""
    return templates.TemplateResponse(request=request, name="scenarios.html")

def _sanitize_json(obj: Any) -> Any:
    """Recursively replace non-finite floats with strings for JSON safety.

    Pydantic numeric constraints reject NaN and infinity, but FastAPI's
    default RequestValidationError handler echoes the raw invalid input
    back in the response. JSONResponse uses allow_nan=False, so a NaN
    response body raises ValueError and the client sees a crash instead
    of a 422. This converts NaN/inf to readable strings before the
    response is serialised.
    """
    if isinstance(obj, float) and not math.isfinite(obj):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_sanitize_json(v) for v in obj]
    return obj


@app.exception_handler(RequestValidationError)
async def _sanitize_validation_error(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return a JSON-safe 422 even when the invalid input was NaN/inf."""
    detail = _sanitize_json(jsonable_encoder(exc.errors()))
    return JSONResponse(status_code=422, content={"detail": detail})

def _check_db_ready() -> bool:
    """Return True when the configured DB responds to a trivial query."""
    db_path = os.environ.get("ECOFAC_DB_PATH", "ecofac_api.db")
    try:
        conn = sqlite3.connect(db_path, timeout=2)
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    else:
        return True


@app.get("/ready", tags=["Health"])
def readiness_check() -> dict:
    """Return 200 when the service can serve traffic (DB reachable).

    Distinct from /health, which is a liveness probe that does not touch
    the database. Load balancers should use /ready for readiness
    decisions and /health for liveness.
    """
    if not _check_db_ready():
        raise HTTPException(status_code=503, detail="Database unreachable")
    return {"status": "ready"}

@app.get("/world", response_class=HTMLResponse, tags=["Virtual Facility"])
def serve_world(request: Request) -> HTMLResponse:
    """Serve the Virtual Facility visualization."""
    return templates.TemplateResponse(request=request, name="world.html")


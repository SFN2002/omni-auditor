"""
Omni-Auditor SaaS Dashboard — Main FastAPI Application.

Entry point that creates the FastAPI app, configures middleware,
mounts all API routers, and handles startup/shutdown events.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from saas.backend.config import settings
from saas.backend.database import init_db

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── Lifespan Events ───────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown events.

    On startup: initialize database tables.
    On shutdown: clean up resources.
    """
    logger.info("Omni-Auditor backend starting up...")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Database URL: {settings.DATABASE_URL.split('@')[-1]}")  # Hide credentials
    logger.info(f"Redis URL: {settings.REDIS_URL}")

    try:
        await init_db()
        logger.info("Database tables initialized successfully")
    except Exception as exc:
        logger.error(f"Failed to initialize database: {exc}")
        # Don't crash — let the app start so health checks can report status

    yield

    logger.info("Omni-Auditor backend shutting down...")


# ── Create FastAPI App ────────────────────────────────────────

app = FastAPI(
    title="Omni-Auditor SaaS Dashboard API",
    description=(
        "RESTful API for the Omni-Auditor SaaS Dashboard. "
        "Provides endpoints for project management, security scanning, "
        "finding analysis, and GitHub webhook integration."
    ),
    version="1.0.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    openapi_url="/openapi.json" if not settings.is_production else None,
    lifespan=lifespan,
)

# ── CORS Middleware ───────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)

# ── Exception Handlers ────────────────────────────────────────


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Handle request validation errors with detailed messages."""
    errors = []
    for error in exc.errors():
        errors.append({
            "loc": error.get("loc", []),
            "msg": error.get("msg", ""),
            "type": error.get("type", ""),
        })

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Validation error",
            "errors": errors,
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Handle unexpected errors gracefully."""
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An internal server error occurred",
        },
    )


# ── API Routers ───────────────────────────────────────────────

from saas.backend.api.auth import router as auth_router
from saas.backend.api.findings import router as findings_router
from saas.backend.api.health import router as health_router
from saas.backend.api.orgs import router as orgs_router
from saas.backend.api.projects import router as projects_router
from saas.backend.api.scans import router as scans_router
from saas.backend.api.webhooks import router as webhooks_router

API_PREFIX = "/api/v1"

app.include_router(auth_router, prefix=f"{API_PREFIX}/auth")
app.include_router(orgs_router, prefix=f"{API_PREFIX}/orgs")
app.include_router(projects_router, prefix=f"{API_PREFIX}/projects")
app.include_router(scans_router, prefix=f"{API_PREFIX}/scans")
app.include_router(findings_router, prefix=f"{API_PREFIX}/findings")
app.include_router(webhooks_router, prefix=f"{API_PREFIX}/webhooks")
app.include_router(health_router, prefix=f"{API_PREFIX}/health")


# ── Root Endpoint ─────────────────────────────────────────────


@app.get("/", tags=["root"])
async def root() -> dict:
    """Root endpoint returning API info."""
    return {
        "name": "Omni-Auditor SaaS Dashboard API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": f"{API_PREFIX}/health",
    }

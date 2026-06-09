"""
Omni-Auditor SaaS Dashboard — Health Check API Routes.

Simple and detailed health check endpoints for monitoring
and load balancer health probes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from saas.backend.config import settings
from saas.backend.database import AsyncSessionLocal, get_db

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=dict)
async def health_check() -> dict:
    """Simple health check — returns OK.

    Used by load balancers and monitoring tools for basic uptime checks.
    """
    return {
        "status": "ok",
        "version": "1.0.0",
        "environment": settings.ENVIRONMENT,
    }


@router.get("/db", response_model=dict)
async def health_db() -> dict:
    """Database connectivity health check.

    Executes a lightweight SQL query to verify database connectivity.
    """
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT 1"))
            row = result.scalar_one()
            if row == 1:
                return {
                    "status": "ok",
                    "database": "connected",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
    except Exception as exc:
        return {
            "status": "error",
            "database": f"disconnected: {exc}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/redis", response_model=dict)
async def health_redis() -> dict:
    """Redis connectivity health check.

    Attempts a PING command to verify Redis connectivity.
    """
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=5,
        )
        pong = await client.ping()
        await client.close()

        if pong:
            return {
                "status": "ok",
                "redis": "connected",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
    except Exception as exc:
        return {
            "status": "error",
            "redis": f"disconnected: {exc}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

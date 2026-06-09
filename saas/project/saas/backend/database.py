"""
Omni-Auditor SaaS Dashboard — Async Database Setup.

SQLAlchemy 2.0 async engine, session factory, and dependency
for FastAPI route handlers.
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from saas.backend.config import settings
from saas.backend.models import Base

# ── Async Engine ──────────────────────────────────────────────

async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.is_development,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    future=True,
)

# ── Session Factory ───────────────────────────────────────────

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ── FastAPI Dependency ────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session for FastAPI dependency injection."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Table Management ──────────────────────────────────────────

async def init_db() -> None:
    """Create all database tables defined in the models."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

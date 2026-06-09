"""
Omni-Auditor SaaS Dashboard — Application Configuration.

Pydantic Settings with environment variable support for all
configuration parameters used across the backend services.
"""

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@db:5432/omniauditor"

    # ── Redis ─────────────────────────────────────────────────
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "redis://redis:6379/1"

    # ── GitHub OAuth ──────────────────────────────────────────
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    GITHUB_WEBHOOK_SECRET: str = ""

    # ── JWT ───────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24

    # ── Omni-Auditor ──────────────────────────────────────────
    OMNI_AUDITOR_PATH: str = "/app/omni-auditor"

    # ── Frontend / CORS ───────────────────────────────────────
    FRONTEND_URL: str = "http://localhost:5173"

    # ── Environment ───────────────────────────────────────────
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    @property
    def is_development(self) -> bool:
        """Return True if running in development mode."""
        return self.ENVIRONMENT.lower() == "development"

    @property
    def is_production(self) -> bool:
        """Return True if running in production mode."""
        return self.ENVIRONMENT.lower() == "production"

    @property
    def cors_origins(self) -> list[str]:
        """Return list of allowed CORS origins."""
        origins = [self.FRONTEND_URL]
        if self.is_development:
            origins.extend([
                "http://localhost:3000",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ])
        return origins


# Global settings singleton
settings = Settings()

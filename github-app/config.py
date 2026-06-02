"""Pydantic settings for the Omni-Auditor GitHub App."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration.

    Required secrets (set via env vars or .env file):
      * OMNI_AUDITOR_APP_ID          — GitHub App ID
      * OMNI_AUDITOR_PRIVATE_KEY     — Path to PEM private key file OR raw PEM string
      * OMNI_AUDITOR_WEBHOOK_SECRET  — Webhook secret for HMAC verification
    """

    model_config = SettingsConfigDict(
        env_prefix="OMNI_AUDITOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_id: str = ""
    private_key: str = ""
    webhook_secret: str = ""
    port: int = 8000
    host: str = "0.0.0.0"
    threshold: float = 0.7
    baseline_dir: str = ".omni_cache/baselines"

    def get_private_key(self) -> str:
        """Return the PEM private key content.

        If ``private_key`` looks like a filesystem path, read it;
        otherwise treat the value as the raw key.
        """
        val = self.private_key.strip()
        if not val:
            return ""
        path = Path(val)
        if path.exists() and path.suffix == ".pem":
            return path.read_text(encoding="utf-8")
        return val


settings = Settings()

"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Backend root: /backend/. Used to resolve relative defaults so the app
# works regardless of where uvicorn / pytest is invoked from.
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Typed settings sourced from `.env` + environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Slate ----
    slate_url: str = Field(default="https://192.168.8.1")
    slate_username: str = Field(default="root")
    slate_password: str = Field(default="changeme")

    # ---- Database ----
    db_url: str = Field(default="sqlite+aiosqlite:///./data/db/slate.db")

    # ---- Auth ----
    jwt_secret: str = Field(default="dev-secret-change-me")
    jwt_algorithm: str = Field(default="HS256")
    jwt_expiration_hours: int = Field(default=24)
    admin_username: str = Field(default="admin")
    admin_password: str = Field(default="change-me")

    # ---- CORS ----
    cors_origins: str = Field(default="http://localhost:5173")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # ---- Logging ----
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    # ---- Profiles ----
    profiles_dir: str = Field(default=str(BACKEND_DIR / "profiles"))


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance."""
    return Settings()

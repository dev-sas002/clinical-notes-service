"""Application configuration, sourced entirely from the environment."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings.

    Every value has a safe default so the service boots with no environment at
    all; production deployments override them via environment variables.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "care-notes-api"
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # SQLAlchemy async URL. SQLite is the tested default; the code itself is
    # engine-agnostic (see README "Design notes" for the caveats).
    database_url: str = Field(default="sqlite+aiosqlite:///./carenotes.db")
    db_echo: bool = Field(default=False)

    # Pagination guard rails. page_size is clamped to max_page_size so a client
    # cannot ask for an unbounded result set.
    default_page_size: int = Field(default=20, ge=1)
    max_page_size: int = Field(default=100, ge=1)

    # Analytics cache. 0 disables caching entirely (NullCache).
    analytics_cache_ttl_seconds: int = Field(default=30, ge=0)
    analytics_cache_max_entries: int = Field(default=512, ge=1)

    # Comma-separated list of origins allowed by CORS.
    cors_allow_origins: str = Field(default="http://localhost:3000")

    # Seeding (used by `python -m app.seed`, never at request time).
    seed_notes: int = Field(default=5_000, ge=0)
    seed_tenants: int = Field(default=3, ge=1)
    seed_facilities_per_tenant: int = Field(default=4, ge=1)
    seed_patients_per_facility: int = Field(default=25, ge=1)
    seed_days: int = Field(default=30, ge=1)
    seed_batch_size: int = Field(default=1_000, ge=1)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor so the env is parsed once per process."""
    return Settings()

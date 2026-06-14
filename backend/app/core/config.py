"""Application configuration, loaded from environment / .env.

Secrets (API keys, DB URL) live in .env and are never committed. See
.env.example for the full list.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://voltpath:voltpath@localhost:5432/voltpath"

    # External APIs (real data). Empty until the operator provisions them.
    nrel_api_key: str = ""
    openchargemap_api_key: str = ""
    mapbox_token: str = ""  # server-side secret (Directions)
    mapbox_public_token: str = ""  # frontend pk.* (map display only)

    charger_cache_ttl_seconds: int = 86_400
    route_cache_ttl_seconds: int = 604_800


@lru_cache
def get_settings() -> Settings:
    return Settings()

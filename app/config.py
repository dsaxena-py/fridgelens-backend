"""Settings, read from environment variables or a .env file (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from fastapi import Request
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres. Use Supabase's Session pooler string: the direct db.<ref>.supabase.co host is IPv6-only.
    database_url: str
    db_pool_min: int = 1
    db_pool_max: int = 5

    supabase_url: str = Field(description="https://<project-ref>.supabase.co")
    supabase_service_role_key: str = ""
    # Supabase signs user tokens with asymmetric keys published at <supabase_url>/auth/v1/.well-known/jwks.json.
    # Older projects use a shared HS256 secret instead; set it here to accept those tokens.
    supabase_jwt_secret: str = ""
    jwt_audience: str = "authenticated"

    storage: Literal["supabase", "memory"] = "supabase"
    storage_bucket: str = "scan-images"
    signed_url_seconds: int = 3600

    # "onnx" loads model_path (with model_card.json beside it); "fake" returns fixed detections
    # so the app can be built and demoed before a model is trained.
    detector: Literal["onnx", "fake"] = "fake"
    model_path: str = "models/meallens.onnx"
    fake_detections: str = "egg:0.92,tomato:0.88,cheese:0.81,onion:0.42"

    max_upload_mb: int = 10
    max_image_side: int = 1600
    # Detections below this confidence are pre-marked for the user to double-check.
    review_confidence: float = 0.5

    cors_origins: list[str] = ["*"]


@lru_cache
def get_settings() -> Settings:
    """Settings from the environment; used once, when the app is created."""
    return Settings()


def app_settings(request: Request) -> Settings:
    """Dependency: the settings the running app was created with."""
    return request.app.state.settings

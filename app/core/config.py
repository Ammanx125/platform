# app/core/config.py
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Sansa"
    environment: str = "development"

    database_url: str

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 14

    llm_provider: str = "mock"
    llm_api_key: str = "INVALID_LOCAL_KEY"
    llm_base_url: str = "http://127.0.0.1:8001/v1"
    model_name: str = "gemma-4-26b-a4b-it"

    # Database pool
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30
    db_echo: bool = False

    # Auth cookies
    access_cookie_name: str = "sansa_access"
    refresh_cookie_name: str = "sansa_refresh"
    csrf_cookie_name: str = "sansa_csrf"
    cookie_domain: str | None = None
    cookie_secure: bool = False          # True in prod
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # CSRF header name expected on state-changing requests
    csrf_header_name: str = "X-CSRF-Token"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    @model_validator(mode="after")
    def _check_jwt_secret_length(self) -> "Settings":
        if len(self.jwt_secret.encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET must be at least 32 bytes")
        return self

    # Storage
    upload_dir: str = "./data/uploads"
    max_upload_bytes: int = 25 * 1024 * 1024  # 25 MB
    allowed_upload_extensions: list[str] = [".csv", ".xlsx", ".xls"]

    # Ingestion worker
    ingestion_worker_poll_seconds: float = 2.0
    ingestion_worker_batch_size: int = 1

        # SQL connector
    sql_row_limit: int = 10000
    sql_timeout_seconds: int = 30

    # HTTP connector
    http_timeout_seconds: int = 30
    http_max_response_bytes: int = 10 * 1024 * 1024  # 10 MB

    # Webhook encryption (see crypto decision)
    webhook_enc_key: str | None = None


settings = Settings()
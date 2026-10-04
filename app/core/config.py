# app/core/config.py
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Sansa"
    environment: str = "development"
    redis_url: str | None = None

    database_url: str

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 14

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
    cookie_secure: bool = False
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
        if (
            self.environment.lower() in {"prod", "production"}
            and not self.cookie_secure
        ):
            raise ValueError(
                "COOKIE_SECURE must be true in production environments"
            )
        if self.redis_url == "":
            self.redis_url = None
        if self.redis_url is not None:
            redis_parts = urlsplit(self.redis_url)
            if redis_parts.scheme not in {"redis", "rediss"} or not redis_parts.netloc:
                raise ValueError("REDIS_URL must include a redis:// or rediss:// host")
        if (
            self.environment.lower() in {"prod", "production"}
            and self.redis_url is None
        ):
            raise ValueError("REDIS_URL is required in production environments")
        if self.sql_row_limit < 1 or self.sql_timeout_seconds < 1:
            raise ValueError("SQL row limit and timeout must be positive")
        if self.sql_stream_batch_size < 1:
            raise ValueError("SQL_STREAM_BATCH_SIZE must be positive")
        if self.retrieval_sql_candidate_limit < 1:
            raise ValueError("RETRIEVAL_SQL_CANDIDATE_LIMIT must be positive")
        if (
            self.customer_behavior_recent_days < 1
            or self.customer_behavior_at_risk_days < self.customer_behavior_recent_days
            or self.customer_behavior_inactive_days < self.customer_behavior_at_risk_days
            or self.customer_behavior_min_purchase_count < 1
            or self.customer_behavior_champion_purchase_count
            < self.customer_behavior_min_purchase_count
            or self.customer_behavior_trend_threshold_pct <= 0
            or not 0 < self.customer_behavior_high_value_percentile <= 100
            or self.customer_behavior_row_limit < 1
        ):
            raise ValueError("customer behavior thresholds and row limit are invalid")
        return self

    # Storage
    upload_dir: str = "./data/uploads"
    max_upload_bytes: int = 25 * 1024 * 1024  # 25 MB
    max_pdf_upload_bytes: int = 100 * 1024 * 1024  # 100 MB
    allowed_upload_extensions: list[str] = [".csv", ".xlsx", ".xls", ".pdf"]

    # Ingestion worker
    ingestion_worker_poll_seconds: float = 2.0
    ingestion_worker_batch_size: int = 1

    # PDF ingestion
    pdf_max_pages: int = 1000
    pdf_min_chars_per_page: int = 10   # below this, page is treated as image-only

    # SQL connector
    sql_row_limit: int = 10000
    sql_timeout_seconds: int = 30
    sql_stream_batch_size: int = 500

    # HTTP connector
    http_timeout_seconds: int = 30
    http_max_response_bytes: int = 10 * 1024 * 1024  # 10 MB

    # Webhook encryption (see crypto decision)
    webhook_enc_key: str | None = None

    # Retrieval / embeddings
    embedding_provider: str = "local"
    embedding_model: str = "intfloat/multilingual-e5-large"
    embedding_dimensions: int = 1024
    fastembed_cache_dir: str = str(
        Path.home() / ".cache" / "sansa" / "fastembed"
    )
    embedding_batch_size: int = 32
    retrieval_default_top_k: int = 10
    retrieval_vector_weight: float = 0.5
    retrieval_keyword_weight: float = 0.3
    retrieval_sql_weight: float = 0.2
    retrieval_sql_candidate_limit: int = 2000

    # Deterministic customer behavior segmentation
    customer_behavior_recent_days: int = 30
    customer_behavior_at_risk_days: int = 90
    customer_behavior_inactive_days: int = 180
    customer_behavior_min_purchase_count: int = 3
    customer_behavior_champion_purchase_count: int = 5
    customer_behavior_trend_threshold_pct: float = 20.0
    customer_behavior_high_value_percentile: float = 75.0
    customer_behavior_row_limit: int = 100_000

    # Chunking
    chunk_target_tokens: int = 400
    chunk_overlap_tokens: int = 50
    chunk_strategy: str = "continuity"
    chunk_heading_prefix: bool = True

    # Forecasting
    forecast_default_horizon: int = 14
    forecast_min_points: int = 10
    forecast_evaluation_metric: str = "mae"       # "mae" | "rmse" | "mape"
    forecast_moving_average_window: int = 7
    forecast_seasonality_min_period: int = 2
    forecast_seasonality_max_fraction: float = 0.33
    forecast_acf_peak_threshold: float = 0.3
    forecast_reliability_high_max_rel_error: float = 0.05
    forecast_reliability_medium_max_rel_error: float = 0.15

    # LLM
    llm_provider: str = "mock"
    llm_api_key: str = "INVALID_LOCAL_KEY"
    llm_base_url: str = "http://127.0.0.1:8001/v1"
    model_name: str = "gemma-4-26b-a4b-it"
    llm_request_timeout_seconds: int = 60
    llm_max_retries: int = 2

    # Security / redaction
    log_redaction_enabled: bool = True

    # Prompt safety
    prompt_injection_score_threshold: float = 0.5
    prompt_untrusted_delimiter: bool = True

    # On-prem agent
    agent_sync_interval_seconds: int = 60
    agent_job_poll_interval_seconds: int = 30
    agent_max_upload_bytes: int = 200 * 1024 * 1024
    agent_job_batch_size: int = 20
    agent_job_in_progress_timeout_seconds: int = 300


# BaseSettings loads required values from the environment at runtime.
settings = Settings()  # pyright: ignore[reportCallIssue]
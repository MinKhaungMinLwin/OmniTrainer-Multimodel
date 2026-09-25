from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OMNI_", env_file=".env", extra="ignore", populate_by_name=True)

    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./omni.db"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "development-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60
    allow_dev_auth: bool = False
    auto_create_schema: bool = False
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://localhost:8081"])
    storage_backend: str = "local"
    attachment_dir: str = "./data/attachments"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "omni"
    s3_secret_key: str = "omni-development-password"
    s3_bucket: str = "omni-attachments"
    s3_server_side_encryption: str | None = None
    max_attachment_bytes: int = 10 * 1024 * 1024
    ai_provider: str = "local"
    ai_model: str = "omni-copilot-local-v1"
    ai_runs_per_minute: int = Field(default=30, ge=1, le=1000)
    ai_timeout_seconds: int = Field(default=15, ge=1, le=120)
    tracing_enabled: bool = False
    tracing_endpoint: str | None = None
    tracing_public_url: str | None = None
    tracing_api_key: str | None = Field(default=None, repr=False)
    tracing_project_name: str = "omni-development"
    tracing_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    tracing_capture_content: bool = False
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "OMNI_GEMINI_API_KEY", "gemini_api_key"),
        repr=False,
    )
    gemini_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_GEMINI_BASE_URL", "OMNI_GEMINI_BASE_URL", "gemini_base_url"),
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "OMNI_OPENAI_API_KEY", "openai_api_key"),
        repr=False,
    )
    openai_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_BASE_URL", "OMNI_OPENAI_BASE_URL", "openai_base_url"),
    )
    openai_reasoning_effort: str = "low"
    gemini_stt_model: str = "gemini-3.5-flash-lite"
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    gemini_tts_voice: str = "Kore"
    ai_max_audio_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    voice_webhook_secret: str = "development-voice-webhook-secret"
    voice_media_secret: str = "development-voice-media-secret-change-me"
    voice_public_base_url: str = "http://localhost:8002"
    voice_max_recording_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    voice_target_first_audio_ms: int = Field(default=800, ge=100, le=10000)
    voice_webhook_tolerance_seconds: int = Field(default=300, ge=10, le=3600)
    voice_max_frame_bytes: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
    voice_max_buffered_frames: int = Field(default=50, ge=1, le=1000)
    voice_stale_frame_ms: int = Field(default=2000, ge=100, le=30000)
    voice_stage_timeout_ms: int = Field(default=1500, ge=50, le=30000)
    voice_circuit_failure_threshold: int = Field(default=3, ge=1, le=100)
    voice_circuit_reset_seconds: int = Field(default=30, ge=1, le=3600)
    voice_max_gateway_sessions: int = Field(default=500, ge=1, le=100000)

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("OMNI_JWT_SECRET must contain at least 32 characters")
        return value

    @field_validator("gemini_base_url", mode="before")
    @classmethod
    def empty_gemini_base_url_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("openai_base_url", mode="before")
    @classmethod
    def empty_openai_base_url_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("tracing_endpoint", mode="before")
    @classmethod
    def empty_tracing_endpoint_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("tracing_public_url", mode="before")
    @classmethod
    def empty_tracing_public_url_is_none(cls, value: str | None) -> str | None:
        return value or None


@lru_cache
def get_settings() -> Settings:
    return Settings()

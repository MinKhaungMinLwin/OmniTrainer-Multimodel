from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OMNI_", env_file=".env", extra="ignore")

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
    max_attachment_bytes: int = 10 * 1024 * 1024

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("OMNI_JWT_SECRET must contain at least 32 characters")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()

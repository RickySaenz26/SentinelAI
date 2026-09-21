import re
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "SentinelAI Backend"
    app_version: str = "0.1.0"
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: str = Field(default="http://localhost:5173")
    database_url: SecretStr | None = None
    session_absolute_minutes: int = Field(default=720, ge=60, le=1440)
    session_idle_minutes: int = Field(default=30, ge=5, le=240)
    csrf_header_name: str = "X-CSRF-Token"
    trusted_origins: str = Field(default="http://localhost:8080")

    @field_validator("csrf_header_name")
    @classmethod
    def validate_csrf_header(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", value):
            raise ValueError("CSRF_HEADER_NAME must be a valid HTTP header name.")
        if value.lower() in {"cookie", "authorization", "origin", "host", "content-type"}:
            raise ValueError("CSRF_HEADER_NAME cannot replace a protocol header.")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.trusted_origins.split(",") if origin.strip()]

    @property
    def database_url_value(self) -> str | None:
        return self.database_url.get_secret_value() if self.database_url else None

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use postgresql+psycopg.")
        return value

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("ENVIRONMENT must be development, test, or production.")
        if self.environment == "production":
            if self.database_url is None:
                raise ValueError("DATABASE_URL is required in production.")
            if not self.cors_origin_list:
                raise ValueError("CORS_ORIGINS must contain at least one production origin.")
            if any(origin.startswith("http://") for origin in self.cors_origin_list):
                raise ValueError("Production CORS_ORIGINS must use HTTPS origins.")
            if any(origin.startswith("http://") for origin in self.trusted_origin_list):
                raise ValueError("Production TRUSTED_ORIGINS must use HTTPS origins.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

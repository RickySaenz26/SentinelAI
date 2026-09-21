import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_cors_origins_are_normalized() -> None:
    configured = Settings(cors_origins="http://localhost:5173, https://example.test ,")

    assert configured.cors_origin_list == ["http://localhost:5173", "https://example.test"]


def test_production_requires_https_cors_origins() -> None:
    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(
            environment="production",
            cors_origins="http://localhost:8080",
            database_url="postgresql+psycopg://user:password@db:5432/sentinelai",
        )


def test_production_accepts_https_cors_origins() -> None:
    configured = Settings(
        environment="production",
        cors_origins="https://app.example.test",
        trusted_origins="https://app.example.test",
        database_url="postgresql+psycopg://user:password@db:5432/sentinelai",
    )

    assert configured.cors_origin_list == ["https://app.example.test"]

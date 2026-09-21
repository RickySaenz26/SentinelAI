import json
import logging
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.core.rate_limit import enforce_rate_limit
from app.main import create_app
from app.platform.crypto import verify_password
from app.platform.database import health
from app.platform.database import session as database


@pytest.mark.parametrize(
    "values,match",
    [
        ({"database_url": "sqlite://"}, "postgresql"),
        ({"environment": "staging"}, "ENVIRONMENT"),
        ({"csrf_header_name": "bad header"}, "HTTP header"),
        ({"csrf_header_name": "Authorization"}, "protocol"),
        ({"environment": "production", "database_url": None}, "required"),
        ({"environment": "production", "cors_origins": ""}, "at least one"),
        (
            {
                "environment": "production",
                "cors_origins": "https://app.test",
                "trusted_origins": "http://app.test",
            },
            "HTTPS",
        ),
    ],
)
def test_configuration_rejects_unsafe_values(values, match):
    with pytest.raises(ValidationError, match=match):
        Settings(**values)


def test_unconfigured_database_and_secret_repr():
    settings = Settings(database_url=None)
    assert settings.database_url_value is None
    configured = Settings(database_url="postgresql+psycopg://example:private@db/test")
    assert "private" not in repr(configured.database_url)


def test_corrupt_password_hash_fails_closed():
    assert verify_password("not-an-argon2-hash", "example") == (False, None)


def test_argon2_rehash_upgrades_old_parameters():
    from argon2 import PasswordHasher

    old_hash = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1).hash("test password")
    valid, replacement = verify_password(old_hash, "test password")
    assert valid
    assert replacement is not None and replacement != old_hash
    assert verify_password(replacement, "test password") == (True, None)


def test_rate_limit_old_entries_expire(monkeypatch):
    from app.core import rate_limit

    timestamps = iter([0.0, 61.0])
    monkeypatch.setattr(rate_limit, "monotonic", lambda: next(timestamps))
    enforce_rate_limit("expiry", "key", limit=1, window_seconds=60)
    enforce_rate_limit("expiry", "key", limit=1, window_seconds=60)


def test_readiness_failure_is_503(client, monkeypatch):
    from app.api.v1 import health as endpoint

    monkeypatch.setattr(endpoint, "is_database_ready", lambda: False)
    assert client.get("/api/v1/health/ready").status_code == 503
    assert client.get("/api/v1/health/live").status_code == 200


def test_database_probe_fails_closed(monkeypatch):
    def unavailable():
        raise RuntimeError("unavailable")

    monkeypatch.setattr(health, "get_engine", unavailable)
    assert health.is_database_ready() is False


def test_unconfigured_engine_fails_closed(monkeypatch):
    monkeypatch.setattr(database, "get_settings", lambda: SimpleNamespace(database_url_value=None))
    with pytest.raises(RuntimeError, match="not configured"):
        database.get_engine()


def test_engine_is_cached_and_replaced_on_url_change(monkeypatch, runtime_engine):
    url = runtime_engine.url.render_as_string(hide_password=False)
    monkeypatch.setattr(database, "get_settings", lambda: SimpleNamespace(database_url_value=url))
    first = database.get_engine()
    assert database.get_engine() is first
    changed = url + "?application_name=closure-test"
    monkeypatch.setattr(
        database, "get_settings", lambda: SimpleNamespace(database_url_value=changed)
    )
    assert database.get_engine() is not first
    database.dispose_engine_for_tests()
    database.dispose_engine_for_tests()


def test_session_factory_initialization_failure(monkeypatch):
    monkeypatch.setattr(database, "get_engine", lambda: None)
    monkeypatch.setattr(database, "_session_factory", None)
    with pytest.raises(RuntimeError, match="not initialized"):
        database.get_session_factory()


def test_structured_logging_exception():
    try:
        raise ValueError("test failure")
    except ValueError:
        record = logging.LogRecord("test", logging.ERROR, "test", 1, "failure", (), sys.exc_info())
    record.request_id = "test-id"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["request_id"] == "test-id"
    assert "ValueError: test failure" in payload["exception"]


def test_unhandled_exception_is_sanitized():
    application = create_app()

    @application.get("/test-fault")
    def fault():
        raise RuntimeError("internal-diagnostic-value")

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/test-fault")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_server_error"
    assert "internal-diagnostic-value" not in response.text

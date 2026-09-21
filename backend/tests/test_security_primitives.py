import pytest

from app.core.errors import ApplicationError
from app.core.rate_limit import enforce_rate_limit, reset_rate_limits
from app.platform.crypto import hash_password, hash_token, verify_password


def test_password_hash_is_not_reversible_and_verifies() -> None:
    password_hash = hash_password("A test-only password with sufficient length")

    assert password_hash != "A test-only password with sufficient length"
    assert verify_password(password_hash, "A test-only password with sufficient length")[0] is True
    assert verify_password(password_hash, "incorrect password")[0] is False


def test_token_hash_is_stable_but_hides_token() -> None:
    token = "opaque-token-for-test-only"

    assert hash_token(token) == hash_token(token)
    assert hash_token(token) != token


def test_rate_limit_returns_retry_after() -> None:
    reset_rate_limits()
    enforce_rate_limit("test", "key", limit=1, window_seconds=60)

    with pytest.raises(ApplicationError) as raised:
        enforce_rate_limit("test", "key", limit=1, window_seconds=60)

    assert raised.value.status_code == 429
    assert raised.value.headers and "Retry-After" in raised.value.headers

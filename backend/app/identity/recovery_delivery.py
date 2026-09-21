"""Recovery delivery port; only test mode can retain an opaque token for assertions."""

from app.core.config import get_settings

_test_messages: list[tuple[str, str]] = []


def deliver_recovery_token(email: str, raw_token: str) -> None:
    """Production delivery is intentionally not configured in Sprint 1B and never logs tokens."""
    if get_settings().environment == "test":
        _test_messages.append((email, raw_token))


def pop_test_message() -> tuple[str, str]:
    if get_settings().environment != "test":
        raise RuntimeError("Recovery message capture is available only in test mode.")
    if not _test_messages:
        raise RuntimeError("No recovery message has been captured.")
    return _test_messages.pop(0)


def clear_test_messages() -> None:
    _test_messages.clear()

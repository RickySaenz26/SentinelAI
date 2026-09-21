"""Shared validation for local administrative identity commands and API contracts."""

from email_validator import EmailNotValidError, validate_email


def normalize_email(value: str) -> str:
    try:
        return validate_email(value, check_deliverability=False).normalized.lower()
    except EmailNotValidError as error:
        raise ValueError("Email is not valid.") from error


def validate_password(value: str) -> None:
    if len(value) < 12 or len(value) > 1024:
        raise ValueError("Password must contain between 12 and 1024 characters.")

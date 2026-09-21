from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_PASSWORD_HASHER = PasswordHasher(
    time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16
)


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, candidate: str) -> tuple[bool, str | None]:
    try:
        valid = _PASSWORD_HASHER.verify(password_hash, candidate)
    except (InvalidHashError, VerifyMismatchError):
        return False, None
    if valid and _PASSWORD_HASHER.check_needs_rehash(password_hash):
        return True, hash_password(candidate)
    return valid, None

"""Argon2id password hashing and verification."""

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError, VerifyMismatchError


_hasher = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=2, hash_len=32, type=Type.ID)
_dummy_hash = _hasher.hash("no-panel-account-uses-this-password")


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("administrator password must contain at least 12 characters")
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        valid = _hasher.verify(password_hash or _dummy_hash, password)
    except (VerificationError, VerifyMismatchError):
        return False
    return bool(valid and password_hash)

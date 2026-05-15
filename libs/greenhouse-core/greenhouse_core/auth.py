"""Password hashing and user repository helpers.

Argon2id via argon2-cffi. JWT issuance lives in the server package; this
module only handles the parts that touch the User model and the password.
"""

from __future__ import annotations

import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from greenhouse_core.models import User

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """Hash a plaintext password with argon2id."""
    if not plain:
        raise ValueError("password must be non-empty")
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verify. Returns False on mismatch or malformed hash."""
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    """True when the stored hash uses outdated argon2 parameters."""
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


def get_user_by_username(session: Session, username: str) -> User | None:
    """Lookup a user by case-sensitive username."""
    return session.scalars(select(User).where(User.username == username)).first()


def get_user(session: Session, user_id: int) -> User | None:
    """Lookup a user by id."""
    return session.get(User, user_id)


def create_user(session: Session, username: str, password: str) -> User:
    """Create a new user with hashed password. Caller commits."""
    user = User(
        username=username,
        hashed_password=hash_password(password),
        is_active=True,
        created_at=int(time.time()),
    )
    session.add(user)
    session.flush()
    return user


def set_password(session: Session, user: User, new_password: str) -> None:
    """Reset a user's password to a new hash. Caller commits."""
    user.hashed_password = hash_password(new_password)
    session.flush()


def record_login(session: Session, user: User) -> None:
    """Stamp last_login_at to now. Caller commits."""
    user.last_login_at = int(time.time())
    session.flush()

"""Repository user methods: the server's one persistence entry point for users (delegates to core auth)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from greenhouse_core import auth as core_auth
from greenhouse_core.models import User

if TYPE_CHECKING:
    from greenhouse_core.repository import IrrigationRepository

LOGIN_AT = 1_700_000_000

PW = "correct horse"


def test_has_users_false_on_empty_db(tmp_db: IrrigationRepository) -> None:
    """A fresh database has no users."""
    assert tmp_db.has_users() is False


def test_create_user_then_lookups(tmp_db: IrrigationRepository) -> None:
    """A created user is active and can be looked up by id and name."""
    user = tmp_db.create_user("Alice", PW)
    assert isinstance(user, User)
    assert user.id is not None
    assert user.is_active is True
    assert core_auth.verify_password(PW, user.hashed_password)
    assert tmp_db.has_users() is True
    assert tmp_db.get_user(user.id) is user
    assert tmp_db.get_user(999) is None
    assert tmp_db.get_user_by_username("Alice") is user
    assert tmp_db.get_user_by_username("alice") is None  # case-sensitive, like core auth


def test_create_user_rejects_empty_password(tmp_db: IrrigationRepository) -> None:
    """Users cannot be created with an empty password."""
    with pytest.raises(ValueError, match="password must be non-empty"):
        tmp_db.create_user("Bob", "")


def test_set_user_password_and_record_login(tmp_db: IrrigationRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing a password re-hashes it, and a login stamps the time."""
    user = tmp_db.create_user("Carol", PW)
    tmp_db.set_user_password(user, "new secret")
    assert core_auth.verify_password("new secret", user.hashed_password)
    monkeypatch.setattr("greenhouse_core.auth.time.time", lambda: 1_700_000_000)
    tmp_db.record_login(user)
    assert user.last_login_at == LOGIN_AT

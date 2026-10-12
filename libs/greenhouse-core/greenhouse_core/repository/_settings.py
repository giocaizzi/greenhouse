"""Operator settings: users, vacation windows, irrigation windows and preferences."""

import time
from typing import Unpack

from sqlalchemy import select

from greenhouse_core import auth
from greenhouse_core.constants import (
    FULL_WEEKDAY_MASK,
)
from greenhouse_core.models import (
    IrrigationWindow,
    User,
    UserPreferences,
    VacationWindow,
)
from greenhouse_core.repository._base import RepositoryBase
from greenhouse_core.repository._types import (
    IrrigationWindowPatch,
    PreferencesPatch,
    VacationWindowPatch,
)


class SettingsMixin(RepositoryBase):
    """Users, vacation windows, irrigation windows and preferences."""

    # ── Users ─────────────────────────────────────────────────────────────────
    # Thin delegates to ``greenhouse_core.auth`` (which owns hashing), so the server reaches
    # users through the repository like every other row. Callers commit.

    def get_user(self, user_id: int) -> User | None:
        """Look up a user by id."""
        return auth.get_user(self.session, user_id)

    def get_user_by_username(self, username: str) -> User | None:
        """Look up a user by case-sensitive username."""
        return auth.get_user_by_username(self.session, username)

    def has_users(self) -> bool:
        """Whether any user row exists (first-run admin bootstrap)."""
        return self.session.scalar(select(User).limit(1)) is not None

    def create_user(self, username: str, password: str) -> User:
        """Create an active user with a hashed password; raises ``ValueError`` on an empty password."""
        return auth.create_user(self.session, username=username, password=password)

    def set_user_password(self, user: User, password: str) -> None:
        """Replace a user's password hash."""
        auth.set_password(self.session, user, password)

    def record_login(self, user: User) -> None:
        """Stamp the user's ``last_login_at`` with the current time."""
        auth.record_login(self.session, user)

    # ── Vacation Windows ──────────────────────────────────────────────────────

    def add_vacation_window(
        self,
        starts_at: int,
        ends_at: int,
        contact_email: str | None = None,
        notes: str | None = None,
    ) -> VacationWindow:
        """Create a vacation window."""
        row = VacationWindow(
            starts_at=starts_at,
            ends_at=ends_at,
            contact_email=contact_email,
            notes=notes,
            created_at=int(time.time()),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_vacation_windows(self) -> list[VacationWindow]:
        """All vacation windows ordered by start time desc."""
        return list(self.session.scalars(select(VacationWindow).order_by(VacationWindow.starts_at.desc())))

    def get_vacation_window(self, window_id: int) -> VacationWindow | None:
        """Fetch a vacation window by id."""
        return self.session.get(VacationWindow, window_id)

    def get_active_vacation(self, at: int | None = None) -> VacationWindow | None:
        """The vacation window active at ``at`` (default: now): ``starts_at <= at < ends_at``.

        The end is exclusive — a vacation whose end time equals now is over.
        """
        now = at or int(time.time())
        return self.session.scalar(
            select(VacationWindow).where(VacationWindow.starts_at <= now, VacationWindow.ends_at > now)
        )

    def update_vacation_window(self, window_id: int, **fields: Unpack[VacationWindowPatch]) -> VacationWindow | None:
        """Patch a vacation window's fields; returns the updated row or None.

        ``None`` clears ``notes`` / ``contact_email`` (the bounds are NOT NULL and
        keep their value) — the route layer is responsible for validating
        semantics (e.g. ``starts_at < ends_at``).
        """
        row = self.session.get(VacationWindow, window_id)
        if not row:
            return None
        self._patch_fields(row, fields)
        self.session.flush()
        return row

    def delete_vacation_window(self, window_id: int) -> bool:
        """Delete a vacation window; returns True if a row was removed."""
        return self._delete_by_id(VacationWindow, window_id)

    # ── Irrigation Windows ────────────────────────────────────────────────────

    def list_irrigation_windows(self, cluster_id: int) -> list[IrrigationWindow]:
        """All windows configured for a cluster, oldest first."""
        return list(
            self.session.scalars(
                select(IrrigationWindow).where(IrrigationWindow.cluster_id == cluster_id).order_by(IrrigationWindow.id)
            )
        )

    def get_irrigation_window(self, window_id: int) -> IrrigationWindow | None:
        """Get an irrigation window by ID."""
        return self.session.get(IrrigationWindow, window_id)

    def add_irrigation_window(
        self,
        cluster_id: int,
        *,
        start_hour: int,
        end_hour: int,
        weekday_mask: int = FULL_WEEKDAY_MASK,
        label: str | None = None,
    ) -> IrrigationWindow:
        """Add an irrigation window to a cluster (every weekday unless a mask is given)."""
        row = IrrigationWindow(
            cluster_id=cluster_id,
            start_hour=start_hour,
            end_hour=end_hour,
            weekday_mask=weekday_mask,
            label=label,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def update_irrigation_window(
        self, window_id: int, **fields: Unpack[IrrigationWindowPatch]
    ) -> IrrigationWindow | None:
        """Patch an irrigation window's fields; ``None`` when it does not exist."""
        row = self.session.get(IrrigationWindow, window_id)
        if row is None:
            return None
        self._patch_fields(row, fields)
        self.session.flush()
        return row

    def delete_irrigation_window(self, window_id: int) -> bool:
        """Delete an irrigation window; ``False`` when it does not exist."""
        return self._delete_by_id(IrrigationWindow, window_id)

    # ── User Preferences (single-row) ─────────────────────────────────────────

    def get_preferences(self) -> UserPreferences:
        """Return the singleton preferences row, creating defaults if missing."""
        prefs = self.session.scalar(select(UserPreferences).limit(1))
        if prefs:
            return prefs
        prefs = UserPreferences()
        self.session.add(prefs)
        self.session.flush()
        return prefs

    def update_preferences(self, **fields: Unpack[PreferencesPatch]) -> UserPreferences:
        """Patch preferences with the provided keyword args (``None`` clears a nullable field; unknown keys ignored)."""
        prefs = self.get_preferences()
        self._patch_fields(prefs, fields)
        self.session.flush()
        return prefs

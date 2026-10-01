"""Scheduling settings: fail fast on bad cadence config, CRON_HOURS beats the legacy interval."""

import pytest
from pydantic import ValidationError

from greenhouse_server.config import Settings
from greenhouse_server.scheduler import _resolve_check_cron_hours

_SCHED_ENV = (
    "IRRIGATION_CHECK_CRON_HOURS",
    "IRRIGATION_CHECK_INTERVAL_HOURS",
    "IRRIGATION_SYNC_INTERVAL_MINUTES",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _SCHED_ENV:
        monkeypatch.delenv(var, raising=False)


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


class TestCronHoursValidation:
    @pytest.mark.parametrize("value", ["*", "0", "0,6,12,18", "*/3", "6-20/2", "23"])
    def test_valid_expressions_accepted(self, value):
        assert _settings(check_cron_hours=value).check_cron_hours == value

    @pytest.mark.parametrize("value", ["bogus", "25", "*/0", "*/48", "", "0,6,"])
    def test_invalid_expression_rejected_naming_env_var(self, value):
        with pytest.raises(ValidationError) as exc:
            _settings(check_cron_hours=value)
        assert "IRRIGATION_CHECK_CRON_HOURS" in str(exc.value)

    def test_invalid_expression_from_env_rejected(self, monkeypatch):
        monkeypatch.setenv("IRRIGATION_CHECK_CRON_HOURS", "every-hour")
        with pytest.raises(ValidationError, match="IRRIGATION_CHECK_CRON_HOURS"):
            _settings()


class TestLegacyIntervalValidation:
    @pytest.mark.parametrize("n", [1, 2, 3, 4, 6, 8, 12])
    def test_divisors_of_24_accepted(self, n):
        assert _resolve_check_cron_hours(_settings(check_interval_hours=n)) == f"*/{n}"

    @pytest.mark.parametrize(
        ("n", "suggestion"),
        [(5, "0,5,10,15,20"), (7, "0,7,14,21"), (24, "0"), (48, "0"), (0, "*"), (-3, "*")],
    )
    def test_invalid_interval_rejected_with_cron_suggestion(self, n, suggestion):
        with pytest.raises(ValidationError) as exc:
            _settings(check_interval_hours=n)
        msg = str(exc.value)
        assert "IRRIGATION_CHECK_INTERVAL_HOURS" in msg
        assert f"IRRIGATION_CHECK_CRON_HOURS={suggestion}" in msg

    def test_ignored_legacy_interval_is_not_validated(self):
        """When CRON_HOURS is set the legacy var is ignored, so it can't fail startup."""
        s = _settings(check_cron_hours="0,12", check_interval_hours=48)
        assert _resolve_check_cron_hours(s) == "0,12"


class TestCronHoursAlwaysWins:
    def test_explicit_star_beats_legacy_interval(self, monkeypatch):
        """Regression: an explicit `*` was indistinguishable from the default,
        so the legacy interval silently overrode it."""
        monkeypatch.setenv("IRRIGATION_CHECK_CRON_HOURS", "*")
        monkeypatch.setenv("IRRIGATION_CHECK_INTERVAL_HOURS", "2")
        assert _resolve_check_cron_hours(_settings()) == "*"

    def test_explicit_star_kwarg_beats_legacy_interval(self):
        assert _resolve_check_cron_hours(_settings(check_cron_hours="*", check_interval_hours=2)) == "*"

    def test_legacy_used_only_when_cron_unset(self, monkeypatch):
        monkeypatch.setenv("IRRIGATION_CHECK_INTERVAL_HOURS", "6")
        assert _resolve_check_cron_hours(_settings()) == "*/6"

    def test_default_is_hourly(self):
        assert _resolve_check_cron_hours(_settings()) == "*"


class TestSyncIntervalValidation:
    @pytest.mark.parametrize("value", [0, -1])
    def test_non_positive_rejected_naming_env_var(self, value):
        with pytest.raises(ValidationError, match="IRRIGATION_SYNC_INTERVAL_MINUTES"):
            _settings(sync_interval_minutes=value)

    def test_positive_accepted(self):
        assert _settings(sync_interval_minutes=1).sync_interval_minutes == 1

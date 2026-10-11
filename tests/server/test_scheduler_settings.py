"""Scheduling settings: fail fast on bad cadence config; the cron hours drive check_all."""

import pytest
from pydantic import ValidationError

from greenhouse_server.config import Settings
from greenhouse_server.scheduler import _resolve_check_cron_hours

_SCHED_ENV = (
    "IRRIGATION_CHECK_CRON_HOURS",
    "IRRIGATION_SYNC_INTERVAL_MINUTES",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _SCHED_ENV:
        monkeypatch.delenv(var, raising=False)


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


class TestCronHoursValidation:
    """Tests for cron hours validation."""

    @pytest.mark.parametrize("value", ["*", "0", "0,6,12,18", "*/3", "6-20/2", "23"])
    def test_valid_expressions_accepted(self, value):
        """Valid expressions accepted."""
        assert _settings(check_cron_hours=value).check_cron_hours == value

    @pytest.mark.parametrize("value", ["bogus", "25", "*/0", "*/48", "", "0,6,"])
    def test_invalid_expression_rejected_naming_env_var(self, value):
        """Invalid expression rejected naming env var."""
        with pytest.raises(ValidationError) as exc:
            _settings(check_cron_hours=value)
        assert "IRRIGATION_CHECK_CRON_HOURS" in str(exc.value)

    def test_invalid_expression_from_env_rejected(self, monkeypatch):
        """Invalid expression from env rejected."""
        monkeypatch.setenv("IRRIGATION_CHECK_CRON_HOURS", "every-hour")
        with pytest.raises(ValidationError, match="IRRIGATION_CHECK_CRON_HOURS"):
            _settings()


class TestCronHoursResolution:
    """Tests for cron hours resolution."""

    def test_default_is_hourly(self):
        """Default is hourly."""
        assert _resolve_check_cron_hours(_settings()) == "*"

    def test_explicit_value_is_used(self, monkeypatch):
        """Explicit value is used."""
        monkeypatch.setenv("IRRIGATION_CHECK_CRON_HOURS", "0,12")
        assert _resolve_check_cron_hours(_settings()) == "0,12"

    def test_removed_legacy_interval_has_no_effect(self, monkeypatch):
        """OD3: IRRIGATION_CHECK_INTERVAL_HOURS is no longer read (it used to become ``*/N``)."""
        monkeypatch.setenv("IRRIGATION_CHECK_INTERVAL_HOURS", "6")
        assert _resolve_check_cron_hours(_settings()) == "*"


class TestSyncIntervalValidation:
    """Tests for sync interval validation."""

    @pytest.mark.parametrize("value", [0, -1])
    def test_non_positive_rejected_naming_env_var(self, value):
        """Non positive rejected naming env var."""
        with pytest.raises(ValidationError, match="IRRIGATION_SYNC_INTERVAL_MINUTES"):
            _settings(sync_interval_minutes=value)

    def test_positive_accepted(self):
        """Positive accepted."""
        assert _settings(sync_interval_minutes=1).sync_interval_minutes == 1

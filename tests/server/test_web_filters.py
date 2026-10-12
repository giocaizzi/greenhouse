"""Unit tests for Jinja2 template filters used by the web UI."""

import time
from typing import ClassVar

from greenhouse_server.web.filters import (
    age_seconds,
    cluster_caps,
    decision_badge,
    icon_for_code,
    moisture_badge,
    stat_position,
    strip_emoji,
    time_ago,
)


class TestStripEmoji:
    """Tests for strip emoji."""

    def test_removes_weather_and_chart_emoji(self) -> None:
        """Removes weather and chart emoji."""
        assert strip_emoji("☁️ low light (68 lux)") == "low light (68 lux)"
        assert strip_emoji("📊 recent under-watering") == "recent under-watering"
        assert strip_emoji("🌑 very low light") == "very low light"

    def test_collapses_internal_separators(self) -> None:
        """Collapses internal separators."""
        text = "soil moisture adequate; ☁️ low light (68 lux); 📊 recent under-watering"
        assert strip_emoji(text) == "soil moisture adequate; low light (68 lux); recent under-watering"

    def test_passes_through_plain_strings(self) -> None:
        """Passes through plain strings."""
        assert strip_emoji("plain prose") == "plain prose"

    def test_handles_none_and_empty(self) -> None:
        """Handles none and empty."""
        assert strip_emoji(None) == ""
        assert strip_emoji("") == ""


class TestTimeAgo:
    """``time_ago`` takes a Unix timestamp."""

    def test_caps_absurd_age_as_stale(self) -> None:
        # Far in the past — old seed data must not render as "20567d ago"
        """Caps absurd age as stale."""
        assert time_ago(0) == "stale"

    def test_handles_none(self) -> None:
        """Handles none."""
        assert time_ago(None) == "—"

    def test_recent_units(self) -> None:
        """Recent units."""
        now = time.time()
        assert time_ago(now - 30).endswith("s ago")
        assert time_ago(now - 300).endswith("m ago")
        assert time_ago(now - 7200).endswith("h ago")
        assert time_ago(now - 2 * 86400).endswith("d ago")

    def test_seven_day_boundary(self) -> None:
        # Just under 7 days -> still d-ago; past it -> stale
        """Seven day boundary."""
        now = time.time()
        assert time_ago(now - (6 * 86400)).endswith("d ago")
        assert time_ago(now - (8 * 86400)) == "stale"


class TestAgeSeconds:
    """``age_seconds`` takes an age in seconds (D18: it used to take a timestamp)."""

    def test_formats_the_age_itself(self) -> None:
        """Formats the age itself."""
        assert age_seconds(30) == "30s ago"
        assert age_seconds(300) == "5m ago"
        assert age_seconds(7200) == "2h ago"
        assert age_seconds(2 * 86400) == "2d ago"

    def test_handles_none(self) -> None:
        """Handles none."""
        assert age_seconds(None) == "—"

    def test_seven_day_boundary(self) -> None:
        """Seven day boundary."""
        assert age_seconds(6 * 86400) == "6d ago"
        assert age_seconds(8 * 86400) == "stale"

    def test_negative_age_reads_zero(self) -> None:
        """Negative age reads zero."""
        assert age_seconds(-5) == "0s ago"


class TestStatPosition:
    """Tests for stat position."""

    def test_centered_value(self) -> None:
        """Centered value."""
        assert stat_position(50, 0, 100) == "50"

    def test_at_lower_bound(self) -> None:
        """At lower bound."""
        assert stat_position(0, 0, 100) == "0"

    def test_at_upper_bound(self) -> None:
        """At upper bound."""
        assert stat_position(100, 0, 100) == "100"

    def test_clamps_below_range(self) -> None:
        """Clamps below range."""
        assert stat_position(-10, 0, 100) == "0"

    def test_clamps_above_range(self) -> None:
        """Clamps above range."""
        assert stat_position(150, 0, 100) == "100"

    def test_falls_back_to_center_on_missing_inputs(self) -> None:
        """Falls back to center on missing inputs."""
        assert stat_position(None, 0, 100) == "50"
        assert stat_position(50, None, 100) == "50"
        assert stat_position(50, 0, None) == "50"
        # Degenerate range
        assert stat_position(50, 100, 100) == "50"

    def test_arbitrary_range(self) -> None:
        """Arbitrary range."""
        assert stat_position(60, 40, 80) == "50"
        assert stat_position(40, 40, 80) == "0"
        assert stat_position(80, 40, 80) == "100"


class TestExistingFiltersStillWork:
    """Sanity check that adding new filters didn't break existing ones."""

    def test_decision_badge(self) -> None:
        """Decision badge."""
        assert decision_badge("irrigate") == "primary"
        assert decision_badge("skip") == "muted"
        assert decision_badge(None) == "muted"

    def test_moisture_badge(self) -> None:
        """Moisture badge."""
        assert moisture_badge(50, 30, 70) == "ok"
        assert moisture_badge(20, 30, 70) == "low"
        assert moisture_badge(80, 30, 70) == "high"


class TestClusterCaps:
    """The capability tier that drives feature gating across the web UI."""

    @staticmethod
    def _status(*, plants: int, sensors: int, irrigator: bool) -> dict[str, object]:
        return {
            "plants": [object()] * plants,
            "sensors": [object()] * sensors,
            "irrigator": {"id": 1} if irrigator else None,
        }

    def test_empty_cluster(self) -> None:
        """Empty cluster."""
        caps = cluster_caps(self._status(plants=0, sensors=0, irrigator=False))
        assert caps["tier"] == "empty"
        assert not caps["can_monitor"]
        assert not caps["can_decide"]
        assert not caps["can_actuate"]
        assert caps["missing"] == ["irrigator", "sensors", "plants"]

    def test_sensor_only_is_full_monitoring_without_actuation(self) -> None:
        """Sensor only is full monitoring without actuation."""
        caps = cluster_caps(self._status(plants=2, sensors=1, irrigator=False))
        assert caps["tier"] == "sensor_only"
        assert caps["can_monitor"]
        assert caps["can_decide"]
        assert not caps["can_actuate"]
        assert caps["missing"] == ["irrigator"]

    def test_plants_only_cannot_monitor(self) -> None:
        """Plants only cannot monitor."""
        caps = cluster_caps(self._status(plants=1, sensors=0, irrigator=False))
        assert caps["tier"] == "sensor_only"
        assert not caps["can_monitor"]
        assert not caps["can_decide"]

    def test_operational_full_stack(self) -> None:
        """Operational full stack."""
        caps = cluster_caps(self._status(plants=1, sensors=1, irrigator=True))
        assert caps["tier"] == "operational"
        assert caps["can_decide"]
        assert caps["can_actuate"]
        assert caps["missing"] == []

    def test_operational_but_degraded_without_sensors(self) -> None:
        """Operational but degraded without sensors."""
        caps = cluster_caps(self._status(plants=1, sensors=0, irrigator=True))
        assert caps["tier"] == "operational"
        assert caps["can_actuate"]
        assert not caps["can_decide"]  # drives the "schedule/manual only" notice

    def test_accepts_orm_like_object(self) -> None:
        """Accepts orm like object."""

        class _Cluster:
            plants: ClassVar[list[object]] = [object()]
            sensors: ClassVar[list[object]] = []
            irrigator = object()

        caps = cluster_caps(_Cluster())
        assert caps["tier"] == "operational"
        assert caps["has_plants"]
        assert not caps["has_sensors"]
        assert caps["has_irrigator"]

    def test_handles_none(self) -> None:
        """Handles none."""
        caps = cluster_caps(None)
        assert caps["tier"] == "empty"


def test_no_action_needed_reason_has_its_icon() -> None:
    """The engine's NO_ACTION_NEEDED reason renders with the "check" icon, not the generic fallback."""
    assert icon_for_code("no_action_needed") == "check"

"""Unit tests for Jinja2 template filters used by the web UI."""

import time

from greenhouse_server.web.filters import (
    age_seconds,
    cluster_caps,
    decision_badge,
    moisture_badge,
    stat_position,
    strip_emoji,
)


class TestStripEmoji:
    def test_removes_weather_and_chart_emoji(self):
        assert strip_emoji("☁️ low light (68 lux)") == "low light (68 lux)"
        assert strip_emoji("📊 recent under-watering") == "recent under-watering"
        assert strip_emoji("🌑 very low light") == "very low light"

    def test_collapses_internal_separators(self):
        text = "soil moisture adequate; ☁️ low light (68 lux); 📊 recent under-watering"
        assert strip_emoji(text) == "soil moisture adequate; low light (68 lux); recent under-watering"

    def test_passes_through_plain_strings(self):
        assert strip_emoji("plain prose") == "plain prose"

    def test_handles_none_and_empty(self):
        assert strip_emoji(None) == ""
        assert strip_emoji("") == ""


class TestAgeSeconds:
    def test_caps_absurd_age_as_stale(self):
        # Far in the past — old seed data must not render as "20567d ago"
        assert age_seconds(0) == "stale"

    def test_handles_none(self):
        assert age_seconds(None) == "—"

    def test_recent_units(self):
        now = time.time()
        assert age_seconds(now - 30).endswith("s ago")
        assert age_seconds(now - 300).endswith("m ago")
        assert age_seconds(now - 7200).endswith("h ago")
        assert age_seconds(now - 2 * 86400).endswith("d ago")

    def test_seven_day_boundary(self):
        # Just under 7 days -> still d-ago; past it -> stale
        now = time.time()
        assert age_seconds(now - (6 * 86400)).endswith("d ago")
        assert age_seconds(now - (8 * 86400)) == "stale"


class TestStatPosition:
    def test_centered_value(self):
        assert stat_position(50, 0, 100) == "50"

    def test_at_lower_bound(self):
        assert stat_position(0, 0, 100) == "0"

    def test_at_upper_bound(self):
        assert stat_position(100, 0, 100) == "100"

    def test_clamps_below_range(self):
        assert stat_position(-10, 0, 100) == "0"

    def test_clamps_above_range(self):
        assert stat_position(150, 0, 100) == "100"

    def test_falls_back_to_center_on_missing_inputs(self):
        assert stat_position(None, 0, 100) == "50"
        assert stat_position(50, None, 100) == "50"
        assert stat_position(50, 0, None) == "50"
        # Degenerate range
        assert stat_position(50, 100, 100) == "50"

    def test_arbitrary_range(self):
        assert stat_position(60, 40, 80) == "50"
        assert stat_position(40, 40, 80) == "0"
        assert stat_position(80, 40, 80) == "100"


class TestExistingFiltersStillWork:
    """Sanity check that adding new filters didn't break existing ones."""

    def test_decision_badge(self):
        assert decision_badge("irrigate") == "primary"
        assert decision_badge("skip") == "muted"
        assert decision_badge(None) == "muted"

    def test_moisture_badge(self):
        assert moisture_badge(50, 30, 70) == "ok"
        assert moisture_badge(20, 30, 70) == "low"
        assert moisture_badge(80, 30, 70) == "high"


class TestClusterCaps:
    """The capability tier that drives feature gating across the web UI."""

    @staticmethod
    def _status(*, plants, sensors, irrigator):
        return {
            "plants": [object()] * plants,
            "sensors": [object()] * sensors,
            "irrigator": {"id": 1} if irrigator else None,
        }

    def test_empty_cluster(self):
        caps = cluster_caps(self._status(plants=0, sensors=0, irrigator=False))
        assert caps["tier"] == "empty"
        assert not caps["can_monitor"] and not caps["can_decide"] and not caps["can_actuate"]
        assert caps["missing"] == ["irrigator", "sensors", "plants"]

    def test_sensor_only_is_full_monitoring_without_actuation(self):
        caps = cluster_caps(self._status(plants=2, sensors=1, irrigator=False))
        assert caps["tier"] == "sensor_only"
        assert caps["can_monitor"] and caps["can_decide"]
        assert not caps["can_actuate"]
        assert caps["missing"] == ["irrigator"]

    def test_plants_only_cannot_monitor(self):
        caps = cluster_caps(self._status(plants=1, sensors=0, irrigator=False))
        assert caps["tier"] == "sensor_only"
        assert not caps["can_monitor"] and not caps["can_decide"]

    def test_operational_full_stack(self):
        caps = cluster_caps(self._status(plants=1, sensors=1, irrigator=True))
        assert caps["tier"] == "operational"
        assert caps["can_decide"] and caps["can_actuate"]
        assert caps["missing"] == []

    def test_operational_but_degraded_without_sensors(self):
        caps = cluster_caps(self._status(plants=1, sensors=0, irrigator=True))
        assert caps["tier"] == "operational"
        assert caps["can_actuate"]
        assert not caps["can_decide"]  # drives the "schedule/manual only" notice

    def test_accepts_orm_like_object(self):
        class _Cluster:
            plants = [object()]
            sensors = []
            irrigator = object()

        caps = cluster_caps(_Cluster())
        assert caps["tier"] == "operational"
        assert caps["has_plants"] and not caps["has_sensors"] and caps["has_irrigator"]

    def test_handles_none(self):
        caps = cluster_caps(None)
        assert caps["tier"] == "empty"

"""Unit tests for Jinja2 template filters used by the web UI."""

import time

from greenhouse_server.web.filters import (
    age_seconds,
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

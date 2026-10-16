"""The CLI mirrors a few core values it may not import (HTTP-only boundary); keep them equal."""

from greenhouse_cli import constants as cli_constants
from greenhouse_core import constants as core_constants
from greenhouse_core.repository import GLOBAL_CONFIG_DEFAULTS


def test_all_weekdays_mirrors_core_full_weekday_mask() -> None:
    """The CLI's every-weekday mask mirrors core's (the CLI may not import core)."""
    assert cli_constants.ALL_WEEKDAYS == core_constants.FULL_WEEKDAY_MASK


def test_time_units_mirror_core() -> None:
    """The CLI's time units mirror core's."""
    assert cli_constants.SECONDS_PER_HOUR == core_constants.SECONDS_PER_HOUR
    assert cli_constants.SECONDS_PER_DAY == core_constants.SECONDS_PER_DAY


def test_global_config_defaults_mirror_core() -> None:
    """The TUI's built-in config defaults mirror the repository's."""
    assert cli_constants.GLOBAL_CONFIG_DEFAULTS == GLOBAL_CONFIG_DEFAULTS

"""The CLI mirrors a few core values it may not import (HTTP-only boundary); keep them equal."""

from greenhouse_cli import constants as cli_constants
from greenhouse_core import constants as core_constants


def test_all_weekdays_mirrors_core_full_weekday_mask():
    assert cli_constants.ALL_WEEKDAYS == core_constants.FULL_WEEKDAY_MASK


def test_time_units_mirror_core():
    assert cli_constants.SECONDS_PER_HOUR == core_constants.SECONDS_PER_HOUR
    assert cli_constants.SECONDS_PER_DAY == core_constants.SECONDS_PER_DAY

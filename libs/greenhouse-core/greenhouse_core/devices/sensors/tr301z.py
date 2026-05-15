"""TR-301Z (zwjcy — Tuya 土壤温湿度) soil temp/humidity sensor adapter.

For PR 1 we reuse the parser table that already lives on ``TuyaCloud``
(``cloud.DATAPOINT_PARSERS``). PR 4 will move the parser table into this
module under ``profile.dp_parsers`` and drop the global, but doing so today
would touch the irrigation pipeline. The profile carries only declarative
bits for now.
"""

from __future__ import annotations

from greenhouse_core.cloud import DATAPOINT_PARSERS, TuyaCloud
from greenhouse_core.devices.profile import SensorProfile, load_profile_json
from greenhouse_core.devices.sensors.tuya_generic import TuyaSensorAdapter


def _load_profile() -> SensorProfile:
    raw = load_profile_json("tr301z.json")
    parsers = {code: DATAPOINT_PARSERS[code] for code in raw.get("_parser_codes", []) if code in DATAPOINT_PARSERS}
    return SensorProfile(
        model_key=raw["model_key"],
        vendor=raw["vendor"],
        transport=raw["transport"],
        capabilities=frozenset(raw.get("capabilities", [])),
        dp_parsers=parsers,
    )


TR301Z_PROFILE = _load_profile()


class TR301ZAdapter(TuyaSensorAdapter):
    """Adapter for the TR-301Z soil temperature + humidity probe."""

    def __init__(self, cloud: TuyaCloud, profile: SensorProfile | None = None):
        super().__init__(profile or TR301Z_PROFILE, cloud)


__all__ = ["TR301ZAdapter", "TR301Z_PROFILE"]

"""ntfy.sh push-notification client.

Outbound push for irrigation events and new alerts. Mirrors
:class:`~greenhouse_server.services.weather.WeatherClient`: synchronous
``urllib`` with a short timeout and a broad ``except`` so a notification can
never raise or block actuation — it always fires *after* the triggering action
is persisted.

ntfy reads the request **body** as the message and takes ``X-Title`` /
``X-Tags`` / ``X-Priority`` headers (priority 1=min … 5=max); a non-ASCII title is
RFC 2047 encoded. A bearer token is sent via ``Authorization`` when configured.
"""

import base64
import logging
import urllib.request
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from greenhouse_core.models import TRIGGERED_BY_EMERGENCY, TriggeredBy
from greenhouse_server.config import check_ntfy_server_url

if TYPE_CHECKING:
    from greenhouse_core.models import Irrigator, Sensor

logger = logging.getLogger(__name__)

# ntfy priority levels (1=min .. 5=max) and emoji tag names per alert severity.
_SEVERITY_PRIORITY = {"critical": "5", "warning": "4", "info": "3"}
_SEVERITY_TAGS = {"critical": "rotating_light", "warning": "warning", "info": "information_source"}

# Irrigation event styling.
_IRRIGATION_TITLES = {
    "manual": "Manual irrigation",
    "emergency": "EMERGENCY stop-all",
    "auto": "Automated irrigation",
}
_IRRIGATION_TAGS = {"manual": "potted_plant", "emergency": "octopus", "auto": "potted_plant"}


def sensor_label(sensor: "Sensor") -> str:
    """``"<plant species> (<cluster>) · <sensor>"``, or ``"<cluster> · <sensor>"`` without a current plant.

    Sensor names repeat across clusters ("Sensore 2"), so an alert or push naming only the
    sensor does not say which plant it is about.
    """
    plant = sensor.plant
    if plant is not None and plant.archived_at is None:
        return f"{plant.species} ({sensor.cluster.name}) · {sensor.name}"
    return f"{sensor.cluster.name} · {sensor.name}"


def irrigator_label(irrigator: "Irrigator") -> str:
    """``"<irrigator> · <cluster>"``: the device name alone does not say which cluster ran."""
    return f"{irrigator.name} · {irrigator.cluster.name}"


def _header_text(text: str) -> str:
    """Encode ``text`` for an HTTP header so ntfy shows it as written.

    urllib sends header values as latin-1, which ntfy reads as UTF-8: an emoji is
    rejected and even ``·`` or ``è`` arrives as ``�``. ntfy decodes RFC 2047
    encoded words in its headers, so non-ASCII text goes as one UTF-8 base64 word.
    """
    if text.isascii():
        return text
    return f"=?UTF-8?B?{base64.b64encode(text.encode()).decode('ascii')}?="


class NtfyClient:
    """Publishes messages to a single ntfy topic."""

    def __init__(self, server_url: str, topic: str, token: str | None = None, timeout: int = 3) -> None:
        """Bind the topic URL; raise ``ValueError`` unless ``server_url`` is http(s)."""
        self._url = check_ntfy_server_url(server_url).rstrip("/") + "/" + topic
        self._token = token
        self._timeout = timeout

    def _publish(self, *, title: str, message: str, tags: str, priority: str) -> bool:
        """POST one notification. Returns True on success; never raises."""
        headers = {
            "X-Title": _header_text(title),
            "X-Tags": tags,
            "X-Priority": priority,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            # S310 is satisfied by construction: __init__ (and Settings) accept only http(s) URLs.
            req = urllib.request.Request(self._url, data=message.encode("utf-8"), headers=headers)  # noqa: S310
            with urllib.request.urlopen(req, timeout=self._timeout):  # noqa: S310
                return True
        except Exception:
            logger.debug("ntfy publish failed", exc_info=True)
            return False

    def notify_irrigation(
        self,
        *,
        triggered_by: TriggeredBy,
        irrigator_name: str,
        duration_minutes: int | None = None,
        detail: str = "",
    ) -> bool:
        """Push an irrigation event (manual / emergency / auto)."""
        dur = f" ({duration_minutes} min)" if duration_minutes else ""
        message = f"{irrigator_name}{dur}"
        if detail:
            message = f"{message} — {detail}"
        return self._publish(
            title=_IRRIGATION_TITLES.get(triggered_by, "Irrigation"),
            message=message,
            tags=_IRRIGATION_TAGS.get(triggered_by, "potted_plant"),
            priority="5" if triggered_by == TRIGGERED_BY_EMERGENCY else "3",
        )

    def notify_alert(self, *, severity: str, title: str, message: str) -> bool:
        """Push a new alert."""
        return self._publish(
            title=title,
            message=message,
            tags=_SEVERITY_TAGS.get(severity, "bell"),
            priority=_SEVERITY_PRIORITY.get(severity, "3"),
        )


NotifyCategory = Literal["manual", "emergency", "alerts", "auto"]
"""The ``notify_<category>`` preference toggles; a typo would silently disable a push."""


def maybe_notify(
    notifier: NtfyClient | None, prefs: object, category: NotifyCategory, fn: Callable[[NtfyClient], object]
) -> None:
    """Call ``fn(notifier)`` (which publishes) only if notifier exists and the category is enabled.

    ``category`` names the ``notify_<category>`` boolean on the preferences row. ``fn`` receives
    the (non-None) client, so callers never touch an unchecked ``notifier``. Fully fail-silent
    so a notification can never disrupt the caller.
    """
    if notifier is None:
        return
    if not getattr(prefs, f"notify_{category}", False):
        return
    try:
        fn(notifier)
    except Exception:
        logger.debug("notification dispatch failed", exc_info=True)

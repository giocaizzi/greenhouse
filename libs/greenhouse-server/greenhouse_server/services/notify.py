"""ntfy.sh push-notification client.

Outbound push for irrigation events and new alerts. Mirrors
:class:`~greenhouse_server.services.weather.WeatherClient`: synchronous
``urllib`` with a short timeout and a broad ``except`` so a notification can
never raise or block actuation — it always fires *after* the triggering action
is persisted.

ntfy reads the request **body** as the message and takes ``X-Title`` /
``X-Tags`` / ``X-Priority`` headers (priority 1=min … 5=max); a bearer token is
sent via ``Authorization`` when configured.
"""

import logging
import urllib.request

log = logging.getLogger(__name__)

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


def _ascii(text: str) -> str:
    """Strip a string to latin-1-safe bytes for use in an HTTP header value.

    urllib rejects non-latin-1 header bytes, so unicode in device/cluster names
    is dropped from the title; emoji live in ``X-Tags`` instead.
    """
    return text.encode("latin-1", "ignore").decode("latin-1")


class NtfyClient:
    """Publishes messages to a single ntfy topic."""

    def __init__(self, server_url: str, topic: str, token: str | None = None, timeout: int = 3):
        self._url = server_url.rstrip("/") + "/" + topic
        self._token = token
        self._timeout = timeout

    def _publish(self, *, title: str, message: str, tags: str, priority: str) -> bool:
        """POST one notification. Returns True on success; never raises."""
        headers = {
            "X-Title": _ascii(title),
            "X-Tags": tags,
            "X-Priority": priority,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            req = urllib.request.Request(self._url, data=message.encode("utf-8"), headers=headers)
            with urllib.request.urlopen(req, timeout=self._timeout):
                return True
        except Exception:
            log.debug("ntfy publish failed", exc_info=True)
            return False

    def notify_irrigation(
        self,
        *,
        triggered_by: str,
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
            priority="5" if triggered_by == "emergency" else "3",
        )

    def notify_alert(self, *, severity: str, title: str, message: str) -> bool:
        """Push a new alert."""
        return self._publish(
            title=title,
            message=message,
            tags=_SEVERITY_TAGS.get(severity, "bell"),
            priority=_SEVERITY_PRIORITY.get(severity, "3"),
        )


def maybe_notify(notifier: NtfyClient | None, prefs, category: str, fn) -> None:
    """Run ``fn`` (which publishes) only if notifier exists and the category is enabled.

    ``category`` is one of ``manual`` / ``emergency`` / ``alerts`` / ``auto``,
    matching the ``notify_<category>`` booleans on the preferences row. Fully
    fail-silent so a notification can never disrupt the caller.
    """
    if notifier is None:
        return
    if not getattr(prefs, f"notify_{category}", False):
        return
    try:
        fn()
    except Exception:
        log.debug("notification dispatch failed", exc_info=True)

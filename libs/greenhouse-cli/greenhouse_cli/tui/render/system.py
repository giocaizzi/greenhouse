"""System screen: scheduler panel, scheduler jobs, device freshness and data-quality issues."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rich.text import Text

from greenhouse_cli.tui import formatting as fmt
from greenhouse_cli.tui.render._rows import Row


def scheduler_panel_rows(*, paused: bool | None, health: dict[str, Any] | None) -> list[tuple[str, str | Text]]:
    """System scheduler panel: automatic-run state, scheduler liveness, last sync and the key hints."""
    return [
        (
            "automatic runs",
            Text("paused", style="bold #e0c341") if paused else Text("active", style="bold #7ed957"),
        ),
        ("scheduler", "running" if (health or {}).get("scheduler_running") else "stopped"),
        ("last sync", fmt.ago((health or {}).get("last_sync_at"))),
        ("", Text("p pause/resume · S sync · P plant DB · H health snapshot · del remove job", style="dim")),
    ]


def job_rows(jobs: Iterable[dict[str, Any]]) -> list[Row]:
    """Scheduler job rows keyed by job id; built-in jobs are tagged."""
    rows: list[Row] = [
        (
            job["id"],
            [
                Text.assemble(job["name"], (" · built-in", "dim") if job.get("core") else ""),
                job["trigger"],
                job.get("next_run_time") or "—",
                Text("paused", style="#e0c341") if job.get("paused") else Text("active", style="#7ed957"),
            ],
        )
        for job in jobs
    ]
    return rows


def device_rows(health: dict[str, Any] | None) -> list[Row]:
    """Device freshness rows (unkeyed) from the system-health payload."""
    rows: list[Row] = [
        (
            None,
            [
                str(d["id"]),
                d["name"],
                fmt.styled(d["status"], fmt.STATUS_STYLES),
                fmt.age(d.get("age_seconds")),
                d.get("note") or "",
            ],
        )
        for d in (health or {}).get("devices", [])
    ]
    return rows


def quality_rows(issues: list[dict[str, Any]]) -> list[Row]:
    """Data-quality issue rows (unkeyed); the entity column carries the id when there is one."""
    rows: list[Row] = []
    for issue in issues:
        entity = issue["entity_type"] + (f" #{issue['entity_id']}" if issue.get("entity_id") is not None else "")
        rows.append(
            (
                None,
                [
                    fmt.styled(issue["severity"], fmt.SEVERITY_STYLES),
                    issue["code"],
                    entity,
                    issue["label"],
                    issue["message"],
                ],
            )
        )
    return rows

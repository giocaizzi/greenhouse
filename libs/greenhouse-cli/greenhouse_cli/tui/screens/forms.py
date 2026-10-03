"""A generic form dialog — every create / edit flow in the TUI is one of these."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from greenhouse_cli.tui.formatting import zone

DATETIME_FORMAT = "%Y-%m-%d %H:%M"


@dataclass
class Field:
    """One form input.

    ``kind`` is ``text`` / ``int`` / ``float`` / ``bool`` / ``select`` /
    ``json`` (object literal) / ``datetime`` (``YYYY-MM-DD HH:MM`` wall time in ``tz``
    — the server's ``timezone`` preference, as the web vacation pages; UTC when unset —
    → Unix seconds). Blank optional
    inputs come back as ``None`` — the client drops ``None`` from update
    bodies, so a blank field means "leave unchanged".
    """

    name: str
    label: str
    kind: str = "text"
    value: Any = None
    options: list[tuple[str, Any]] = field(default_factory=list)
    required: bool = False
    placeholder: str = ""
    tz: str | None = None


def parse_value(f: Field, raw: Any) -> Any:
    """Convert a widget value to the field's type; raise ``ValueError`` on bad input."""
    if f.kind == "bool":
        return bool(raw)
    if f.kind == "select":
        return None if raw is Select.NULL else raw
    text = (raw or "").strip()
    if not text:
        if f.required:
            raise ValueError(f"{f.label} is required")
        return None
    try:
        if f.kind == "int":
            return int(text)
        if f.kind == "float":
            return float(text)
        if f.kind == "datetime":
            return int(datetime.strptime(text, DATETIME_FORMAT).replace(tzinfo=zone(f.tz)).timestamp())
        if f.kind == "json":
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError
            return parsed
    except ValueError:
        hints = {"datetime": "YYYY-MM-DD HH:MM", "json": "a JSON object", "int": "a whole number"}
        hint = hints.get(f.kind, f"a {f.kind}")
        raise ValueError(f"{f.label}: expected {hint}") from None
    return text


def _display(f: Field) -> str:
    if f.value is None:
        return ""
    if f.kind == "datetime":
        return datetime.fromtimestamp(f.value, zone(f.tz)).strftime(DATETIME_FORMAT)
    if f.kind == "json":
        return json.dumps(f.value)
    return str(f.value)


class FormScreen(ModalScreen[dict[str, Any] | None]):
    """Render ``fields`` and dismiss with ``{name: parsed value}`` or ``None``."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel"), ("ctrl+s", "submit", "Save")]

    def __init__(self, title: str, fields: list[Field], submit_label: str = "Save", note: str | None = None) -> None:
        super().__init__()
        self.form_title = title
        self.fields = fields
        self.submit_label = submit_label
        self.note = note

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog form-dialog"):
            yield Static(f"[b]{self.form_title}[/b]", classes="dialog-message")
            if self.note:
                yield Static(self.note, classes="hint")
            with VerticalScroll(classes="form-body"):
                for f in self.fields:
                    with Horizontal(classes="form-row"):
                        if f.kind == "bool":
                            yield Checkbox(f.label, value=bool(f.value), id=f"field-{f.name}")
                            continue
                        yield Label(f.label + (" *" if f.required else ""), classes="form-label")
                        if f.kind == "select":
                            yield Select(
                                f.options,
                                value=f.value if f.value is not None else Select.NULL,
                                allow_blank=not f.required,
                                id=f"field-{f.name}",
                            )
                        else:
                            yield Input(
                                _display(f),
                                placeholder=f.placeholder or ("YYYY-MM-DD HH:MM" if f.kind == "datetime" else ""),
                                type="integer" if f.kind == "int" else "number" if f.kind == "float" else "text",
                                id=f"field-{f.name}",
                            )
            yield Label("", id="form-error", classes="dialog-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button(self.submit_label, variant="primary", id="submit")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.action_submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self) -> None:
        self.action_submit()

    def action_submit(self) -> None:
        values: dict[str, Any] = {}
        try:
            for f in self.fields:
                widget = self.query_one(f"#field-{f.name}")
                values[f.name] = parse_value(f, widget.value)  # type: ignore[attr-defined]
        except ValueError as e:
            self.query_one("#form-error", Label).update(str(e))
            return
        self.dismiss(values)

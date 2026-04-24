"""Jinja2 templates singleton with shared filters."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from tuya_irrigation_server.web import filters

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

for name, fn in filters.ALL_FILTERS.items():
    templates.env.filters[name] = fn

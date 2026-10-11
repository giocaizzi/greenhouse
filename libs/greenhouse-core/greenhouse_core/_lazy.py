"""PEP 562 lazy re-exports shared by the core package ``__init__`` modules.

Python initialises every parent package before a submodule, so an ``__init__``
that eagerly re-exports its heavy members makes ``import
greenhouse_core.constants`` (or ``greenhouse_core.devices.health``) pay for
SQLAlchemy, Alembic, the engine and ``tinytuya``. A package built with
:func:`lazy_module_attrs` resolves those names on first attribute access
instead, caches them in its namespace and still lists them in ``dir()``.
"""

import importlib
from collections.abc import Callable, Mapping
from typing import Any


def lazy_module_attrs(
    package: str,
    namespace: dict[str, Any],
    exports: Mapping[str, str],
    modules: Mapping[str, str],
) -> tuple[Callable[[str], Any], Callable[[], list[str]]]:
    """Build a package's module-level ``__getattr__`` and ``__dir__``.

    Args:
        package: The package's ``__name__`` (used in the ``AttributeError``).
        namespace: The package's ``globals()``; resolved names are cached there
            so ``__getattr__`` runs at most once per name.
        exports: Re-exported name -> absolute name of the module defining it.
        modules: Attribute name -> absolute module name for whole modules the
            package exposes (its submodules, or a re-exported third-party module).

    Returns:
        The ``(__getattr__, __dir__)`` pair to bind at package level.
    """

    def _getattr(name: str) -> Any:
        if name in exports:
            value = getattr(importlib.import_module(exports[name]), name)
        elif name in modules:
            value = importlib.import_module(modules[name])
        else:
            msg = f"module {package!r} has no attribute {name!r}"
            raise AttributeError(msg)
        namespace[name] = value
        return value

    def _dir() -> list[str]:
        return sorted(set(namespace) | set(exports) | set(modules))

    return _getattr, _dir

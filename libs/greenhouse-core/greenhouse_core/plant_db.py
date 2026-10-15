"""Plant database lookup - Evidence-based plant care data."""

import copy
import functools
import json
import logging
import os
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from greenhouse_core.constants import PLANT_CARE_FALLBACK

if TYPE_CHECKING:  # annotation only: importing schemas at runtime would load Pydantic and SQLAlchemy
    from greenhouse_core.schemas import SoilBandSource

logger = logging.getLogger(__name__)

# One plant-database entry (species, category or mapping row) as decoded from JSON.
CareData = dict[str, Any]

# IRRIGATION_PLANT_DB_PATH is read once at import: the fallback for direct core users
# (tests, scripts, PlantDatabase() without a path). The server resolves the same
# variable through Settings.plant_db_path and builds its PlantDatabase from that.
_DEFAULT_PLANT_DB_PATH = Path(str(files("greenhouse_core") / "data" / "plant_database.json"))
PLANT_DB_PATH = (
    Path(os.environ["IRRIGATION_PLANT_DB_PATH"])
    if os.environ.get("IRRIGATION_PLANT_DB_PATH")
    else _DEFAULT_PLANT_DB_PATH
)


class PlantDatabase:
    """Lookup plant care requirements from scientific literature."""

    def __init__(self, db_path: Path | None = None) -> None:
        """Load the curated database from ``db_path`` (default: the bundled JSON)."""
        self.db_path = db_path or PLANT_DB_PATH
        self._data = self._load_database()

    def _load_database(self) -> dict[str, Any]:
        """Load plant database from JSON."""
        if not self.db_path.exists():
            msg = f"Plant database not found: {self.db_path}"
            raise FileNotFoundError(msg)

        with self.db_path.open(encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        return data

    def lookup_species(self, species: str) -> CareData | None:
        """Look up care requirements for a specific species.

        Args:
            species: Scientific name or common name (e.g., "Monstera deliciosa", "Areca palm")

        Returns:
            dict with care requirements or None if not found
        """
        table: dict[str, CareData] = self._data["species"]
        species = species.strip()
        # Exact match
        if species in table:
            return table[species]

        # Check aliases (case-insensitive)
        for _spec_name, spec_data in table.items():
            aliases = spec_data.get("aliases", [])
            if species in aliases or species.lower() in [a.lower() for a in aliases]:
                return spec_data

        # Fuzzy match: check if species name contains any database key or alias
        species_lower = species.lower()
        for _spec_name, spec_data in table.items():
            # Check if database name is in species string
            if _spec_name.lower() in species_lower:
                return spec_data
            # Check if any alias is in species string
            aliases = spec_data.get("aliases", [])
            for alias in aliases:
                if alias.lower() in species_lower:
                    return spec_data

        return None

    def lookup_category(self, category: str) -> CareData | None:
        """Look up general care requirements for a plant category.

        Category keys are lower-case; the name is trimmed and lower-cased first,
        so a plant stored as ``"Tropical"`` still resolves.

        Args:
            category: Category name (e.g., "tropical", "succulent")

        Returns:
            dict with care requirements or None if not found
        """
        categories: dict[str, CareData] = self._data["categories"]
        return categories.get(_category_key(category))

    def get_care_data(self, species: str | None = None, category: str | None = None) -> CareData:
        """Resolve plant care data with three-layer precedence.

        Layered merge (least → most specific): ultimate defaults < ``categories[c]``
        biology basics < ``_category_defaults[c]`` timing fields < ``species[s]``
        species-specific overrides. ``_category_defaults`` is also exposed
        verbatim under the ``_category_defaults`` key so the decision engine
        can keep the category layer distinct (needed by
        :func:`greenhouse_core.logic.timing.seasonal_multiplier` for per-season
        fallback).

        Args:
            species: Scientific or common name. Resolved species short-circuits
                the category lookup for the biology-basics layer.
            category: Category name. Used to source both ``categories[c]`` and
                ``_category_defaults[c]``. When ``species`` resolves, the
                resolved species' own ``category`` field is preferred over the
                argument.

        Returns:
            Merged care dict — always populated. Includes ``category`` (resolved
            category name, when known) and ``_category_defaults`` (the
            category's timing override block, or ``{}``).
        """
        species_data, resolved_category = self._resolve(species, category)

        # Deep copy: callers own the returned dict, including its ``sources`` list.
        merged: CareData = copy.deepcopy(PLANT_CARE_FALLBACK)

        if resolved_category:
            cat_basics = self.lookup_category(resolved_category)
            if cat_basics:
                merged.update(cat_basics)
            cat_defaults = self._data.get("_category_defaults", {}).get(resolved_category, {})
            merged.update(cat_defaults)
            merged["_category_defaults"] = cat_defaults
            merged["category"] = resolved_category
        else:
            merged["_category_defaults"] = {}

        if species_data:
            cat_defaults_snapshot = merged.get("_category_defaults", {})
            merged.update(species_data)
            merged["_category_defaults"] = cat_defaults_snapshot
            if resolved_category:
                merged["category"] = resolved_category

        return merged

    def soil_band_source(self, species: str | None = None, category: str | None = None) -> "SoilBandSource":
        """Name the layer whose ``soil_moisture_target`` :meth:`get_care_data` returns for this plant.

        The most specific layer that carries the key wins, as in the merge:
        species > ``_category_defaults[c]`` > ``categories[c]``; with none of
        them, the band is the built-in fallback (``builtin_fallback``).
        """
        species_data, resolved_category = self._resolve(species, category)
        key = "soil_moisture_target"
        if species_data and key in species_data:
            return "species"
        if resolved_category and key in self._data.get("_category_defaults", {}).get(resolved_category, {}):
            return "category_defaults"
        if resolved_category and key in self._data["categories"].get(resolved_category, {}):
            return "category"
        return "builtin_fallback"

    def _resolve(self, species: str | None, category: str | None) -> tuple[CareData | None, str | None]:
        """The matched species entry and the normalised category; a miss is logged, never silent."""
        species_data = self.lookup_species(species) if species else None
        resolved_category = _category_key((species_data or {}).get("category") or category or "") or None
        if species and species_data is None:
            logger.debug("Plant DB: species %r not found", species)
        if resolved_category and resolved_category not in self._data["categories"]:
            logger.debug("Plant DB: category %r not found", resolved_category)
        return species_data, resolved_category

    def get_water_needs_info(self, water_needs: str) -> CareData:
        """Get detailed info for a water_needs level."""
        mapping: dict[str, CareData] = self._data["water_needs_mapping"]
        return mapping.get(water_needs, mapping["medium"])

    def list_species(self) -> list[str]:
        """List all species in database."""
        return list(self._data["species"].keys())

    def list_categories(self) -> list[str]:
        """List all categories in database."""
        return list(self._data["categories"].keys())

    def get_metadata(self) -> CareData:
        """Get database metadata (version, sources, etc.)."""
        metadata: CareData = self._data["_metadata"]
        return metadata


def _category_key(category: str) -> str:
    """Normalise a category name to the database's lower-case keys."""
    return category.strip().lower()


# Convenience singleton: built on first use, dropped by ``reset_plant_database``.
@functools.cache
def get_plant_database() -> PlantDatabase:
    """Get singleton plant database instance."""
    return PlantDatabase()


def reset_plant_database() -> None:
    """Reset the singleton instance (for testing)."""
    get_plant_database.cache_clear()

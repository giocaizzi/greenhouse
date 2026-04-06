"""Plant database lookup - Evidence-based plant care data."""

import json
import os
from pathlib import Path

_DEFAULT_PLANT_DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "plant_database.json"
PLANT_DB_PATH = (
    Path(os.environ["IRRIGATION_PLANT_DB_PATH"])
    if os.environ.get("IRRIGATION_PLANT_DB_PATH")
    else _DEFAULT_PLANT_DB_PATH
)


class PlantDatabase:
    """Lookup plant care requirements from scientific literature."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or PLANT_DB_PATH
        self._data = self._load_database()

    def _load_database(self) -> dict:
        """Load plant database from JSON."""
        if not self.db_path.exists():
            raise FileNotFoundError(f"Plant database not found: {self.db_path}")

        with open(self.db_path, encoding="utf-8") as f:
            return json.load(f)

    def lookup_species(self, species: str) -> dict | None:
        """
        Look up care requirements for a specific species.

        Args:
            species: Scientific name or common name (e.g., "Monstera deliciosa", "Areca palm")

        Returns:
            dict with care requirements or None if not found
        """
        # Exact match
        if species in self._data["species"]:
            return self._data["species"][species]

        # Check aliases (case-insensitive)
        for _spec_name, spec_data in self._data["species"].items():
            aliases = spec_data.get("aliases", [])
            if species in aliases or species.lower() in [a.lower() for a in aliases]:
                return spec_data

        # Fuzzy match: check if species name contains any database key or alias
        species_lower = species.lower()
        for _spec_name, spec_data in self._data["species"].items():
            # Check if database name is in species string
            if _spec_name.lower() in species_lower:
                return spec_data
            # Check if any alias is in species string
            aliases = spec_data.get("aliases", [])
            for alias in aliases:
                if alias.lower() in species_lower:
                    return spec_data

        return None

    def lookup_category(self, category: str) -> dict | None:
        """
        Look up general care requirements for a plant category.

        Args:
            category: Category name (e.g., "tropical", "succulent")

        Returns:
            dict with care requirements or None if not found
        """
        return self._data["categories"].get(category)

    def get_care_data(self, species: str | None = None, category: str | None = None) -> dict:
        """
        Get care data with fallback: species → category → defaults.

        Args:
            species: Scientific or common name
            category: Category name

        Returns:
            dict with care requirements (always returns data, with fallback)
        """
        # Try species-specific first
        if species:
            data = self.lookup_species(species)
            if data:
                return data

        # Fall back to category
        if category:
            data = self.lookup_category(category)
            if data:
                return data

        # Ultimate fallback: medium tropical (safest default)
        return {
            "water_needs": "medium",
            "water_frequency_days": 7,
            "ideal_temp_min_c": 18,
            "ideal_temp_max_c": 27,
            "ideal_humidity_min": 50,
            "ideal_humidity_max": 70,
            "light_needs": "medium",
            "soil_moisture_target": "45-65",
            "sources": ["fallback default"],
        }

    def get_water_needs_info(self, water_needs: str) -> dict:
        """Get detailed info for a water_needs level."""
        return self._data["water_needs_mapping"].get(water_needs, self._data["water_needs_mapping"]["medium"])

    def get_light_needs_info(self, light_needs: str) -> dict:
        """Get detailed info for a light_needs level."""
        return self._data["light_needs_mapping"].get(light_needs, self._data["light_needs_mapping"]["medium"])

    def list_species(self) -> list[str]:
        """List all species in database."""
        return list(self._data["species"].keys())

    def list_categories(self) -> list[str]:
        """List all categories in database."""
        return list(self._data["categories"].keys())

    def get_metadata(self) -> dict:
        """Get database metadata (version, sources, etc.)."""
        return self._data["_metadata"]


# Convenience singleton
_db_instance = None


def get_plant_database() -> PlantDatabase:
    """Get singleton plant database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = PlantDatabase()
    return _db_instance


def set_plant_database(instance: PlantDatabase) -> None:
    """Set the singleton instance (for custom path configuration)."""
    global _db_instance
    _db_instance = instance


def reset_plant_database() -> None:
    """Reset the singleton instance (for testing)."""
    global _db_instance
    _db_instance = None

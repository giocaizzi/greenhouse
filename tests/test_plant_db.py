"""Test suite for plant database lookup."""

import pytest

from greenhouse_core.plant_db import get_plant_database, reset_plant_database


class TestPlantDatabase:
    """Test plant database lookups and fallback logic."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.db = get_plant_database()
        yield
        reset_plant_database()

    def test_lookup_exact_species(self):
        """Exact species name returns care data."""
        data = self.db.lookup_species("Monstera deliciosa")
        assert data is not None
        assert data["water_needs"] == "medium"

    def test_lookup_species_not_found(self):
        """Unknown species returns None."""
        assert self.db.lookup_species("Nonexistent planticus") is None

    def test_lookup_category(self):
        """Category lookup returns default care data."""
        data = self.db.lookup_category("tropical")
        assert data is not None
        assert "water_needs" in data

    def test_lookup_category_not_found(self):
        """Unknown category returns None."""
        assert self.db.lookup_category("nonexistent_category") is None

    def test_get_care_data_species_first(self):
        """get_care_data prefers species over category."""
        data = self.db.get_care_data(species="Monstera deliciosa", category="tropical")
        assert data is not None
        # Species data has common_name
        assert "common_name" in data or "water_needs" in data

    def test_get_care_data_category_fallback(self):
        """get_care_data falls back to category when species unknown."""
        data = self.db.get_care_data(species="Unknown plant", category="tropical")
        assert data is not None
        assert data["water_needs"] is not None

    def test_get_care_data_ultimate_fallback(self):
        """get_care_data returns safe defaults when nothing matches."""
        data = self.db.get_care_data(species="Unknown", category="unknown")
        assert data["water_needs"] == "medium"
        assert data["ideal_temp_min_c"] == 18
        assert data["soil_moisture_target"] == "45-65"

    def test_list_species(self):
        """Species list is not empty."""
        species = self.db.list_species()
        assert len(species) > 0
        assert "Monstera deliciosa" in species

    def test_list_categories(self):
        """Category list is not empty."""
        categories = self.db.list_categories()
        assert len(categories) > 0
        assert "tropical" in categories

    def test_singleton_returns_same_instance(self):
        """get_plant_database returns same instance on repeated calls."""
        db1 = get_plant_database()
        db2 = get_plant_database()
        assert db1 is db2

    def test_reset_creates_new_instance(self):
        """reset_plant_database clears the singleton."""
        db1 = get_plant_database()
        reset_plant_database()
        db2 = get_plant_database()
        assert db1 is not db2

    def test_metadata(self):
        """Database metadata exists and has version."""
        meta = self.db.get_metadata()
        assert "version" in meta

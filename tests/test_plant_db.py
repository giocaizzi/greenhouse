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

    def test_category_defaults_surface_for_unknown_species(self):
        """Unknown species + known category → timing fields come from _category_defaults."""
        data = self.db.get_care_data(species="Some-Unknown-Species", category="tropical")
        assert data["category"] == "tropical"
        # The tropical category default has a preferred-hours window and a
        # seasonal multiplier — both must appear in the merged dict.
        assert "preferred_water_hours_local" in data
        assert isinstance(data["preferred_water_hours_local"], list)
        assert len(data["preferred_water_hours_local"]) == 2
        assert "season_frequency_multiplier" in data
        # The raw category-default block is also exposed so the engine can use
        # it as a distinct override layer.
        assert "preferred_water_hours_local" in data["_category_defaults"]

    def test_species_overrides_category_defaults(self):
        """Species-level timing fields take precedence over _category_defaults values."""
        data = self.db.get_care_data(species="Eriobotrya japonica")
        # Loquat sets species-level preferred hours [5,9] in plant_database.json.
        assert data["preferred_water_hours_local"] == [5, 9]
        # And a species-level outdoor multiplier that differs from the
        # fruit_tree category default.
        species_outdoor = data["season_frequency_multiplier_outdoor"]
        cat_outdoor = data["_category_defaults"].get("season_frequency_multiplier_outdoor")
        assert species_outdoor != cat_outdoor

    def test_category_defaults_inherited_when_species_silent(self):
        """Species with no timing override inherits both timing fields from category defaults."""
        data = self.db.get_care_data(species="Monstera deliciosa")
        # Monstera carries only ``category: tropical`` — timing fields must
        # flow through from _category_defaults.
        cat_defaults = data["_category_defaults"]
        assert data["preferred_water_hours_local"] == cat_defaults["preferred_water_hours_local"]
        assert data["season_frequency_multiplier"] == cat_defaults["season_frequency_multiplier"]

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

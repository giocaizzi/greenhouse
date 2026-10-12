"""The PATCH semantics of the repository ``update_*`` methods (B-15).

An explicit ``None`` clears a nullable column and is skipped for a NOT NULL one (the
stored value is kept), keys that are not attributes of the row are ignored, and every
other supplied key is set. Absent keys are never touched — the routes pass only the
fields the client sent (``model_dump(exclude_unset=True)``).
"""

from typing import Any

from greenhouse_core.models import Cluster, Plant, UserPreferences
from greenhouse_core.repository import IrrigationRepository


def test_update_cluster_none_clears_nullable_skips_required_ignores_unknown_keys(tmp_db: IrrigationRepository) -> None:
    """Cluster PATCH: None clears nullable fields, skips required ones and unknown keys are ignored."""
    cluster_id = tmp_db.add_cluster("Patch Cluster", "Shelf")

    unknown: dict[str, Any] = {"bogus_field": "x"}  # not a patch key; routes pass model_dump()'s dict[str, Any]
    updated = tmp_db.update_cluster(cluster_id, name=None, location=None, environment="outdoor", **unknown)

    assert isinstance(updated, Cluster)
    assert (updated.name, updated.location, updated.environment) == ("Patch Cluster", None, "outdoor")
    assert not hasattr(updated, "bogus_field")


def test_update_cluster_absent_keys_are_unchanged(tmp_db: IrrigationRepository) -> None:
    """Cluster PATCH leaves absent keys unchanged."""
    cluster_id = tmp_db.add_cluster("Patch Cluster", "Shelf")

    updated = tmp_db.update_cluster(cluster_id, environment="outdoor")

    assert updated is not None
    assert (updated.name, updated.location, updated.environment) == ("Patch Cluster", "Shelf", "outdoor")


def test_update_plant_none_clears_nullable_and_sets_the_rest(tmp_db: IrrigationRepository) -> None:
    """Plant PATCH: None clears nullable fields and the other values are set."""
    cluster_id = tmp_db.add_cluster("Patch Cluster")
    plant_id = tmp_db.add_plant(cluster_id, "Monstera deliciosa")
    tmp_db.update_plant(plant_id, notes="kept", ideal_temp_min=18.0)

    unknown: dict[str, Any] = {"bogus_field": 1}  # not a patch key; routes pass model_dump()'s dict[str, Any]
    updated = tmp_db.update_plant(plant_id, notes=None, category="tropical", **unknown)

    assert isinstance(updated, Plant)
    assert (updated.notes, updated.category, updated.ideal_temp_min) == (None, "tropical", 18.0)
    assert not hasattr(updated, "bogus_field")


def test_update_preferences_none_skips_required_clears_nullable(tmp_db: IrrigationRepository) -> None:
    """Preferences PATCH: None skips required fields and clears nullable ones."""
    cluster_id = tmp_db.add_cluster("Patch Cluster")
    tmp_db.update_preferences(theme="dark", default_cluster_id=cluster_id)

    unknown: dict[str, Any] = {"bogus_field": True}  # not a patch key; routes pass model_dump()'s dict[str, Any]
    prefs = tmp_db.update_preferences(theme=None, default_cluster_id=None, refresh_interval_seconds=60, **unknown)

    assert isinstance(prefs, UserPreferences)
    assert (prefs.theme, prefs.default_cluster_id, prefs.refresh_interval_seconds) == ("dark", None, 60)
    assert not hasattr(prefs, "bogus_field")


def test_update_vacation_window_none_clears_notes_and_email_keeps_bounds(tmp_db: IrrigationRepository) -> None:
    """Vacation PATCH: None clears notes and email but keeps the time bounds."""
    window = tmp_db.add_vacation_window(1_000, 2_000, contact_email="a@example.invalid", notes="trip")

    updated = tmp_db.update_vacation_window(window.id, starts_at=None, notes=None, contact_email=None)

    assert updated is not None
    assert (updated.starts_at, updated.ends_at, updated.notes, updated.contact_email) == (1_000, 2_000, None, None)

"""Characterization: the PATCH semantics of ``update_cluster`` / ``update_plant`` / ``update_preferences``.

All three skip ``None`` values (the stored value is kept), silently ignore keys that
are not attributes of the row, and set every other supplied key. Pinned before the
two private PATCH helpers are merged into one.
"""

from greenhouse_core.models import Cluster, Plant, UserPreferences


def test_update_cluster_skips_none_ignores_unknown_keys_and_sets_the_rest(tmp_db):
    cluster_id = tmp_db.add_cluster("Patch Cluster", "Shelf")

    updated = tmp_db.update_cluster(cluster_id, name=None, location="Window", bogus_field="x", environment="outdoor")

    assert isinstance(updated, Cluster)
    assert (updated.name, updated.location, updated.environment) == ("Patch Cluster", "Window", "outdoor")
    assert not hasattr(updated, "bogus_field")


def test_update_plant_skips_none_ignores_unknown_keys_and_sets_the_rest(tmp_db):
    cluster_id = tmp_db.add_cluster("Patch Cluster")
    plant_id = tmp_db.add_plant(cluster_id, "Monstera deliciosa")
    tmp_db.update_plant(plant_id, notes="kept")

    updated = tmp_db.update_plant(plant_id, notes=None, category="tropical", bogus_field=1)

    assert isinstance(updated, Plant)
    assert (updated.notes, updated.category) == ("kept", "tropical")
    assert not hasattr(updated, "bogus_field")


def test_update_preferences_skips_none_ignores_unknown_keys_and_sets_the_rest(tmp_db):
    tmp_db.update_preferences(theme="dark")

    prefs = tmp_db.update_preferences(theme=None, refresh_interval_seconds=60, bogus_field=True)

    assert isinstance(prefs, UserPreferences)
    assert (prefs.theme, prefs.refresh_interval_seconds) == ("dark", 60)
    assert not hasattr(prefs, "bogus_field")

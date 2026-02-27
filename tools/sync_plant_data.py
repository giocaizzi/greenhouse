#!/usr/bin/env python3
"""Update existing plants with data from plant database."""

import argparse
import sys
from pathlib import Path

# Add src to path for package imports
skill_root = Path(__file__).parent.parent
sys.path.insert(0, str(skill_root / "src"))

from tuya_irrigation.db import IrrigationDB  # noqa: E402
from tuya_irrigation.plant_db import get_plant_database  # noqa: E402


def update_plant_from_db(db: IrrigationDB, plant_id: int, plant_db: dict):
    """Update a plant with data from plant database."""
    # Get plant's current data
    plants = db.get_plants_in_cluster(1)  # Assuming cluster 1
    plant = next((p for p in plants if p.id == plant_id), None)
    if not plant:
        print(f"Plant {plant_id} not found")
        return False

    # Get care data from plant database
    care_data = plant_db.get_care_data(species=plant.species, category=plant.category)

    # Update plant in database
    conn = db.conn
    conn.execute(
        """UPDATE plants
           SET water_needs = ?,
               light_needs = ?,
               ideal_temp_min = ?,
               ideal_temp_max = ?,
               ideal_humidity_min = ?,
               ideal_humidity_max = ?,
               notes = ?
           WHERE id = ?""",
        (
            care_data.get("water_needs"),
            care_data.get("light_needs"),
            care_data.get("ideal_temp_min_c"),
            care_data.get("ideal_temp_max_c"),
            care_data.get("ideal_humidity_min"),
            care_data.get("ideal_humidity_max"),
            f"Sources: {', '.join(care_data.get('sources', [])[:2])}",
            plant_id,
        ),
    )
    conn.commit()

    print(f"✅ Updated {plant.species}:")
    print(f"   Water: {care_data.get('water_needs')}")
    print(f"   Light: {care_data.get('light_needs')}")
    print(f"   Temp: {care_data.get('ideal_temp_min_c')}-{care_data.get('ideal_temp_max_c')}°C")
    print(f"   Humidity: {care_data.get('ideal_humidity_min')}-{care_data.get('ideal_humidity_max')}%")
    return True


def update_all_plants(db: IrrigationDB):
    """Update all plants in database with data from plant database."""
    plant_db = get_plant_database()

    clusters = db.list_clusters()
    updated = 0

    for cluster in clusters:
        plants = db.get_plants_in_cluster(cluster.id)
        print(f"\n📦 {cluster.name} ({len(plants)} plants)")

        for plant in plants:
            care_data = plant_db.get_care_data(
                species=plant.species,
                category=plant.category
            )

            # Update in database
            conn = db.conn
            conn.execute(
                """UPDATE plants
                   SET water_needs = ?,
                       light_needs = ?,
                       ideal_temp_min = ?,
                       ideal_temp_max = ?,
                       ideal_humidity_min = ?,
                       ideal_humidity_max = ?,
                       notes = ?
                   WHERE id = ?""",
                (
                    care_data.get("water_needs"),
                    care_data.get("light_needs"),
                    care_data.get("ideal_temp_min_c"),
                    care_data.get("ideal_temp_max_c"),
                    care_data.get("ideal_humidity_min"),
                    care_data.get("ideal_humidity_max"),
                    f"Sources: {', '.join(care_data.get('sources', [])[:2])}",
                    plant.id,
                ),
            )
            conn.commit()

            print(f"  ✅ {plant.species}")
            print(f"     Water: {care_data.get('water_needs')} | Temp: {care_data.get('ideal_temp_min_c')}-{care_data.get('ideal_temp_max_c')}°C")
            updated += 1

    print(f"\n✅ Updated {updated} plants with evidence-based data")


def main():
    parser = argparse.ArgumentParser(
        description="Update plants with data from scientific plant database"
    )
    parser.add_argument(
        "--plant-id",
        type=int,
        help="Update specific plant by ID (omit to update all)",
    )
    parser.add_argument(
        "--db",
        help="Database path (default: auto)",
    )
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else None
    db = IrrigationDB(db_path)
    plant_db = get_plant_database()

    try:
        if args.plant_id:
            update_plant_from_db(db, args.plant_id, plant_db)
        else:
            update_all_plants(db)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

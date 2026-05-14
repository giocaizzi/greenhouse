# Plant database reference

The server's per-plant decisions read care requirements from a curated JSON file shipped inside `greenhouse-core` at `libs/greenhouse-core/greenhouse_core/data/plant_database.json`. This file describes the shape of that data so you can help the user read a target, extend the database, or interpret why a plant has the moisture range it does.

## Lookup order

When the engine needs care data for a plant, it walks this fallback chain and uses the first hit:

1. **Species-specific entry** — keyed by binomial name, e.g. `"Monstera deliciosa"`.
2. **Category default** — keyed by category name, e.g. `"tropical"`, `"succulent"`, `"cacti"`, `"fern"`, `"fruit_tree"`.
3. **Hard-coded engine defaults** — last resort.

Always prefer species over category when both are available. The category fallback exists so a freshly-added plant without species coverage still gets reasonable targets instead of engine defaults.

## Schema

### Species entry

```json
"Monstera deliciosa": {
  "common_name": "Swiss Cheese Plant",
  "category": "tropical",
  "water_needs": "medium",
  "water_frequency_days": 7,
  "ideal_temp_min_c": 18,
  "ideal_temp_max_c": 29,
  "ideal_humidity_min": 60,
  "ideal_humidity_max": 80,
  "soil_moisture_target": "45-60",
  "light_needs": "medium",
  "sources": ["The Spruce: ...", "..."]
}
```

### Category entry

Same shape as a species entry, minus `common_name` and `category`. Used as the fallback when a species isn't in the database.

### Fields

| Field | Type | Notes |
|---|---|---|
| `common_name` | string | Human-readable name. Species only. |
| `category` | string | Links a species to its category fallback. Species only. |
| `water_needs` | `"low"` / `"medium"` / `"high"` | Coarse band; the engine combines this with sensor readings. |
| `water_frequency_days` | int | Base cadence in days. Adjusted by sensors and weather at runtime. |
| `ideal_temp_min_c` / `ideal_temp_max_c` | float | Celsius. The engine raises stress reasons outside this band. |
| `ideal_humidity_min` / `ideal_humidity_max` | percent (0–100) | Ambient, not soil. |
| `soil_moisture_target` | string `"min-max"` | The band the engine aims for; below `min` → `sensor_dry`, above `max` → `sensor_wet`. |
| `light_needs` | `"low"` / `"medium"` / `"high"` | Used by daytime-lux alerts. |
| `sources` | array of strings | Provenance — at least two independent sources per entry. The engine doesn't read these, but they exist to justify the numbers. |

## Helping the user add a species

If the user wants to add a new plant species:

1. Confirm they have at least two independent sources for water / temperature / humidity targets. This is a hard convention — single-source entries get rejected on review.
2. Edit `libs/greenhouse-core/greenhouse_core/data/plant_database.json` and add the species entry with all required fields, including the `sources` array.
3. Run `greenhouse plant sync` (or `greenhouse plant sync --plant-id N` for a single plant) to push the new care data into existing `Plant` rows on the server.

When in doubt about a target value, prefer the **safer band** — narrower humidity range, narrower moisture target, conservative temperature limits. Overwatering and out-of-band stress are harder to recover from than slightly suboptimal care.

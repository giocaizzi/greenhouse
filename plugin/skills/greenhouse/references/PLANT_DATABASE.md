# Plant database reference

The server's per-plant decisions read care requirements from a curated JSON file shipped inside `greenhouse-core` at `libs/greenhouse-core/greenhouse_core/data/plant_database.json`. This file describes the shape of that data so you can help the user read a target, extend the database, or interpret why a plant has the moisture range it does.

## Top-level structure

The JSON is a single object with these top-level keys:

- `species` — object keyed by binomial name (e.g. `"Monstera deliciosa"`) → species care entry.
- `categories` — object keyed by category name (`"tropical"`, `"succulent"`, `"cacti"`, `"fern"`, `"fruit_tree"`) → biology-basics entry.
- `_category_defaults` — object keyed by category name → **timing-only** override block (`preferred_water_hours_local`, `season_frequency_multiplier`, optional `season_frequency_multiplier_outdoor`, `sources`). Kept distinct from `categories` so the engine can apply the category timing layer separately.
- `water_needs_mapping` / `light_needs_mapping` — coarse band → descriptive metadata used for thresholds/alerts.
- `_metadata`, `notes` — provenance and human-readable notes; the engine does not key on these.

## Resolution — `get_care_data`

`plant_db.get_care_data(species, category)` does **not** return the first matching tier. It builds a result by a **layered merge** (least → most specific), where each later layer overrides keys from the earlier ones:

1. **Hard-coded engine defaults** — always present as a base (`water_needs: "medium"`, `water_frequency_days: 7`, temp 18–27 °C, humidity 50–70, `soil_moisture_target: "45-65"`, etc.).
2. **`categories[c]` biology basics** — merged in when a category resolves.
3. **`_category_defaults[c]` timing fields** — merged in on top of category basics.
4. **`species[s]` species-specific overrides** — merged last, so species wins on every shared key.

The resolved category is the species' own `category` field when a species matched, otherwise the `category` argument. The returned dict always carries `category` (resolved name, when known) and `_category_defaults` (the category's timing block verbatim, or `{}`) — the latter is exposed separately so `logic.timing.seasonal_multiplier` can fall back to the category layer per season even after species overrides are applied.

### Species matching

`lookup_species` resolves a name in three passes: exact key match, then case-insensitive `aliases` match, then fuzzy substring match (database key or any alias contained in the supplied name). This is why a `Plant` row stored as `"Areca palm"` still resolves to `"Dypsis lutescens"`.

## Schema

### Species entry (`species[…]`)

```json
"Monstera deliciosa": {
  "common_name": "Swiss Cheese Plant",
  "category": "tropical",
  "native_region": "Central America",
  "water_needs": "medium",
  "water_frequency_days": 7,
  "water_notes": "Water when top 2 inches of soil dry. Moderate humidity needed.",
  "ideal_temp_min_c": 18,
  "ideal_temp_max_c": 29,
  "ideal_humidity_min": 60,
  "ideal_humidity_max": 80,
  "light_needs": "medium",
  "soil_moisture_target": "45-60",
  "ideal_light_lux_min": 150,
  "sources": ["The Spruce: ...", "..."]
}
```

Species may also carry `aliases`, the timing fields (`preferred_water_hours_local`, `season_frequency_multiplier`, `season_frequency_multiplier_outdoor`), and a `timing_notes` string — but only when literature shows a real deviation from the category default. Species without a timing override inherit it from `_category_defaults[category]`.

### Category entry (`categories[…]`)

Biology-basics for a category — same care fields as a species entry plus a `description`, minus species-only fields (`common_name`, `category`, `aliases`, `native_region`, `ideal_light_lux_min`). Merged under species data, above engine defaults.

### Category timing block (`_category_defaults[…]`)

A separate top-level object holding **only** the timing fields for each category, plus its own `sources`. Merged above `categories[c]` and below the species entry, and also returned verbatim so the engine keeps the category timing layer distinct (see *Resolution*). Example:

```json
"tropical": {
  "preferred_water_hours_local": [7, 10],
  "season_frequency_multiplier": { "winter": 0.5, "spring": 1.0, "summer": 1.2, "autumn": 0.8 },
  "sources": ["..."]
}
```

### Fields

| Field | Type | Notes |
|---|---|---|
| `common_name` | string | Human-readable name. Species only. |
| `category` | string | Links a species to its category. Species only. |
| `aliases` | array of strings | Alternate names matched case-insensitively, then by fuzzy substring. Species only, optional. |
| `native_region` | string | Provenance/biogeography. Species only, optional. The engine doesn't key on it. |
| `water_needs` | `"low"` / `"medium"` / `"high"` | Coarse band; the engine combines this with sensor readings. |
| `water_frequency_days` | int | Base cadence in days. Adjusted by sensors and weather at runtime. |
| `water_notes` | string | Human-readable care note. The engine doesn't read it. Optional. |
| `ideal_temp_min_c` / `ideal_temp_max_c` | float | Celsius. The engine raises stress reasons outside this band. |
| `ideal_humidity_min` / `ideal_humidity_max` | percent (0–100) | Ambient, not soil. |
| `soil_moisture_target` | string `"min-max"` | The band the engine aims for; below `min` → `sensor_dry`, above `max` → `sensor_wet`. |
| `light_needs` | `"low"` / `"medium"` / `"high"` | Used by daytime-lux alerts. |
| `ideal_light_lux_min` | int | Minimum adequate lux for this species. Species only, optional. |
| `preferred_water_hours_local` | `[start_hour, end_hour]` | Local-hour window the engine prefers to water in. Lives on `_category_defaults[c]`; a species may override. |
| `season_frequency_multiplier` | object `{winter,spring,summer,autumn}` → float | Per-season scaling of base frequency (indoor / default). On `_category_defaults[c]`; species may override. |
| `season_frequency_multiplier_outdoor` | object `{winter,spring,summer,autumn}` → float | Outdoor variant; the engine prefers it when `cluster.environment == "outdoor"`. Optional, on category and/or species. |
| `timing_notes` | string | Rationale for a timing override. Species only, optional. The engine doesn't read it. |
| `sources` | array of strings | Provenance — at least two independent sources per entry. The engine doesn't read these, but they exist to justify the numbers. |

## Helping the user add a species

If the user wants to add a new plant species:

1. Confirm they have at least two independent sources for water / temperature / humidity targets. This is a hard convention — single-source entries get rejected on review.
2. Edit `libs/greenhouse-core/greenhouse_core/data/plant_database.json` and add the species entry under `species` with all required fields, including the `sources` array. Leave out the timing fields (`preferred_water_hours_local`, `season_frequency_multiplier{,_outdoor}`) unless the literature shows a real deviation from the category — otherwise the species inherits them from `_category_defaults[category]`.
3. Run `greenhouse plant sync` (or `greenhouse plant sync --plant-id N` for a single plant) to push the new care data into existing `Plant` rows on the server.

When in doubt about a target value, prefer the **safer band** — narrower humidity range, narrower moisture target, conservative temperature limits. Overwatering and out-of-band stress are harder to recover from than slightly suboptimal care.

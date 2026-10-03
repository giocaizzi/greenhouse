# POST-WP8 hand-off — D11, D16b, OD3 device-type aliases, devices typing, ratchet

Worktree `/home/user/gh-pw-a`, branch `refactor/post-wp8`, based on `f63b2f3` (integration gate after WP8).
Not pushed, not merged, not rebased. `greenhouse_core/sync.py` and repo-wide StrEnum signatures were not touched.

## Commits (one concern each, in order)

| Task | Commit | Kind | Evidence |
|---|---|---|---|
| D11 | `60829bf` | `refactor(web)` cluster_detail quiet flag via `logic.timing.active_quiet_window` | `$WEB` + cluster pages + irrigator actions → 309 passed; goldens unchanged (incl. `cluster_detail__quiet_hours_now`, `irrigate__quiet_hours*`) |
| D16b pin | `1f99fee` | `test(devices)` 21 config input shapes through `resolve_local_key` / `open_local` + the same table on `parse_device_config` | 50 passed on the unmodified gateway |
| D16b | `5addfe0` | `refactor(devices)` gateway uses `models.parse_device_config`; `_coerce_config` deleted | identical on every pinned input (the extra `TypeError` catch was dead: `json.loads(str)` never raises it); `$DEV $CORE C(gc.devices.gateway)` → 1143 passed |
| OD3 prep | `2eaca6b` | `test:` fixtures/test data → canonical keys | 240 literals / 64 files (AST scan) + fake registries drop legacy keys; with the aliases still present the suite was green except 37 goldens, regenerated; every golden line diff = legacy value → canonical key (3 edit-form lines lose `selected`, explained below) |
| OD3 CLI | `3d7544a` | `docs(cli)` `--type` help on irrigator/sensor add/update | doc-contract: only 4 help goldens + `params.json`, 8 help-text lines |
| OD3 web | `dc64396` | `fix(consistency)` forms offer `rainpoint.ik10pw` / `tuya.tr301z` | 9 goldens (7 form pages, 2 create-irrigator re-renders): option lines, hint/legend text, removed `<script>` |
| OD3 registry | `2258a20` | `fix(consistency)` alias tables removed; failure mode pinned | new `tests/server/test_legacy_device_types.py` (7 tests); registry golden removal-only; pipeline `unknown_irrigator_model` reason text (1 line); plugin `references/CLI.md`, README, REFACTOR_NOTES entry; mutants devices-49/50/51 re-targeted → all KILLED |
| typing | `f1cc4ad` | `refactor(devices)` strict-clean `gateway.py` + `ik10pw.py` | AST identical to parent after stripping annotations / type-only classes; both added to `refactor/mypy-strict.txt` |
| stubs | `f3885b1` | `build(types)` global `ignore_missing_imports` → override for `tinytuya`, `apscheduler`, `fastapi_mcp` | dropping the global flag gives exactly 7 import-untyped errors, all in those three |
| ratchet | `528d63f` | `chore(lint)` registry per-file ignores → `["N818"]` (+2 docstrings) | gateway still fires every listed code (unchanged) |

Branch order is the DY ordering fix: nothing offers a legacy value (`3d7544a` CLI, `dc64396` web) before the registry
stops resolving it (`2258a20`). No commit leaves a broken state.

## OD3 decisions (look here hardest)

- **Scope:** both alias tables, including the `""` entries and the sensor aliases. The sensor web forms offered only
  `soil_moisture` / `temp_humidity` / `light`, so they changed too, and so did the CLI `sensor --type` help.
- **Legacy rows in old databases (decided, logged, pinned):** Alembic `6c9d4e2f3a12` rewrote legacy values (leftovers and `""` now rewritten by `a1d3f5b7c902`, owner decision), but rows
  added later through the old web form or CLI help still carry them. The owner's live DB **probably has such an
  irrigator** (the web form offered only `tuya_cloud` / `tuya_local`). After this branch that irrigator is refused:
  `get_irrigator` logs an ERROR (type + known keys + irrigator id) and raises `UnknownDeviceModel`. As a result,
  manual start returns 503 with that detail, automatic or forced runs skip with `no adapter for irrigator: …`,
  stop-all lists the irrigator under `errors`, and the health poll logs it. A legacy sensor still syncs readings
  but gets no health monitoring (WARNING on each poll). Remedies: `irrigator update <cluster> --type rainpoint.ik10pw`,
  `sensor update <id> --cluster N --type tuya.tr301z`, or saving the web edit form (it now always submits the
  canonical key). **Suggest to the owner:** run
  `UPDATE irrigators SET type='rainpoint.ik10pw' WHERE type IN ('tuya_cloud','tuya_local','')` (and the matching
  sensors update) before deploying. A new data-only Alembic revision would do this automatically, but I did not
  add one: it is out of scope (`migrations/versions/*` and the Alembic head are frozen), so it needs an owner call.
- **Miss messages changed:** `(resolved='X')` was a meaningless echo once the aliases were gone. It is now
  `(known: <sorted keys>)`. The sensor warning also names the sensor id.
- **Pre-existing form bug fixed along the way:** the edit forms listed only legacy options, so editing any
  canonical row silently submitted `tuya_cloud` / `soil_moisture`. This is why 3 edit-form golden lines lost
  `selected` in `2eaca6b`.
- The irrigator local-protocol fieldset is now always shown. Its JS hid it unless the type was `tuya_local`, which
  meant it was always hidden for the IK10PW, whose cycle bounding is local-only.

## Strict list / config
- `refactor/mypy-strict.txt` additions: `devices/gateway.py` and `devices/irrigators/ik10pw.py`, inserted in place
  without reordering, so a merge resolves as a plain union.
- `open_local(protocol_version)` is now typed `float | None`, matching `IrrigatorProfile.protocol_version`.
  This is annotation-only; the value passes through to `set_version` unchanged.
- `[tool.mypy]`: the global `ignore_missing_imports` was removed and replaced by a `[[tool.mypy.overrides]]` entry
  for the three packages.

## Gates (on `528d63f`)
ruff check 0 · ruff format --check 0 · `make typecheck` 0 (172 files) · `lint-imports` 0 (20 kept) ·
`sizecheck` exit 1, only from `sync.py::sync_sensor_data` / `sync_single_sensor`. Those are pre-existing: the file is
owned by another package and this branch has no diff in it. FULL (seed 0, `-n 2`, lock): **3136 passed, exit 0** (8m50s; 3125 at the fixture-switch run + 50 config pins − rewritten alias tests + 7 legacy-type pins).

## For the integrator / follow-ups
- `refactor/gate1/mutate.py`: the gateway snippet now calls `parse_device_config`, and the registry mutants 49–51
  were re-targeted. `--check` still reports 355/470, unchanged from base.
- Dead code (not removed, test-only users): `DeviceRegistry.registered_*_keys` (unchanged); the `fake.irrigator` /
  `fake.sensor` keys in the test fake registries (no row uses them).
- gateway per-file ignores (BLE001 EM101 EM102 PLR0915 TRY003) all still fire. Converting BLE001 to per-line noqa
  and the EM/TRY raises to `msg = …` would be a separate ratchet.
- Merge conflict hazard with `refactor/types-final`: tests and goldens touched by `2eaca6b` (64 test files), plus
  `pyproject.toml` `[tool.mypy]`.

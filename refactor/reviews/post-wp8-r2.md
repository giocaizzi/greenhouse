# Post-WP8 — second review (devices, high risk)

Range `f63b2f3..8355fad` on `refactor/post-wp8`. **Overall: APPROVE**, no blockers, three nits.
Method: detached worktrees at both ends; full suite at 8355fad 3137 passed; lint-imports 20 kept; typecheck clean.

| Commit | Verdict | Evidence |
|---|---|---|
| 5addfe0 D16b gateway → `parse_device_config` | OK | 38 input classes (dict variants, JSON object/array/scalar/malformed/BOM/NaN/surrogate strings, deep nesting, None/bytes/numbers/containers/object) through old `_coerce_config` and new path, plus `resolve_local_key` (cold/warm/refresh) and `open_local` (3.5/None/"3.5"/3.3): JSON dumps byte-identical (3886 lines) incl. Cloud call counts, cache, OutletDevice args |
| f1cc4ad typing gateway/ik10pw | OK | AST identical with annotations/docstrings stripped (only `typing` import, type-only `_CloudDevice`/`_TuyaCloudClient`); ik10pw unchanged; `open_local` value passed through untouched |
| 2eaca6b fixture switch | OK | word diff: type-string swaps only (97 files under tests/); golden moves follow from fixture data |
| 3d7544a CLI help | OK | `--type` help strings only |
| dc64396 web forms | OK, nit 1 | only the type select, its help/legend, and the JS that hid the local fieldset; nit: edit form hard-selects the single canonical option — render from `registry.registered_*_keys()` if a second model is added |
| 2258a20 alias removal | OK, nit 2 | 13 type values: canonical keys resolve identically; legacy/""/variants raise `UnknownDeviceModel` (ERROR) / sensor None (WARNING); every caller degrades (manual 503, pipeline "no adapter", stop-all errors, shutdown/pump-watcher catch, health poll per-irrigator catch); nit: docstring credited only `6c9d4e2f3a12` — fixed in 737cdcb |
| 8355fad migration a1d3f5b7c902 | OK, nit 3 | mixed rows at 9f2b5e7c6a31 → head rewritten exactly per the removed alias tables (variants untouched); schema identical; idempotent; downgrade/upgrade stable. Nit: API/CLI/MCP may still store an unknown type (logged each health tick with traceback) — optional labeled follow-up: validate `type` on write (422) |
| 60829bf D11 | OK | 725,200 cases (start/end incl. None, out-of-range, strings, floats, bools, wrap, start==end; 8 timezones incl. invalid; 74 instants incl. DST): zero differences, exceptions identical |

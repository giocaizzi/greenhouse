# Changelog

All notable changes to this project are tracked here. Managed automatically by
[release-please](https://github.com/googleapis/release-please) from
[Conventional Commits](https://www.conventionalcommits.org/) — do not hand-edit.

## [2.1.0](https://github.com/giocaizzi/greenhouse/compare/v2.0.0...v2.1.0) (2026-05-14)


### Added

* **api:** add .well-known OAuth metadata stubs for MCP HTTP client compatibility ([#22](https://github.com/giocaizzi/greenhouse/issues/22)) ([698b021](https://github.com/giocaizzi/greenhouse/commit/698b021f5f4e49a61403e5aa96575c72ae3209c9))
* **plugin:** add Claude Code marketplace and plugin for agent installs ([#17](https://github.com/giocaizzi/greenhouse/issues/17)) ([68a5c46](https://github.com/giocaizzi/greenhouse/commit/68a5c4620341adb17bbbe91346f5a7b0ac2bc222))
* **web:** show app version in footer ([#20](https://github.com/giocaizzi/greenhouse/issues/20)) ([7d12ef9](https://github.com/giocaizzi/greenhouse/commit/7d12ef9cd71f9ae336bd53ba6b472d07584cc18a))


### Fixed

* **plants:** move plant sensors with the plant on cluster reassignment ([#21](https://github.com/giocaizzi/greenhouse/issues/21)) ([6c2f5cf](https://github.com/giocaizzi/greenhouse/commit/6c2f5cf562f2f2e1d397f168fabb2e1cc5835f65))

## [2.0.0](https://github.com/giocaizzi/greenhouse/compare/v1.0.0...v2.0.0) (2026-05-14)


### ⚠ BREAKING CHANGES

* **scheduler:** IRRIGATION_CHECK_INTERVAL_HOURS removed; use IRRIGATION_CHECK_CRON_HOURS (hours list, default "*").

### Added

* **api:** move a plant between clusters ([#13](https://github.com/giocaizzi/greenhouse/issues/13)) ([1d3ecb5](https://github.com/giocaizzi/greenhouse/commit/1d3ecb51bfd35c169231214b2fce34d725fd4a64)), closes [#10](https://github.com/giocaizzi/greenhouse/issues/10)
* **api:** runtime pause/resume for check_all scheduler job ([#14](https://github.com/giocaizzi/greenhouse/issues/14)) ([ed56cd5](https://github.com/giocaizzi/greenhouse/commit/ed56cd5e6d40f4d620f603bcb4ae3d9b496ddea4)), closes [#8](https://github.com/giocaizzi/greenhouse/issues/8)
* **mcp:** require bearer token for /mcp (fail-closed when unset) ([#11](https://github.com/giocaizzi/greenhouse/issues/11)) ([0e598e9](https://github.com/giocaizzi/greenhouse/commit/0e598e9a36671872c52ee6783dc5d1e23b57fa56)), closes [#9](https://github.com/giocaizzi/greenhouse/issues/9)
* **scheduler:** cron-based check_all with backward-compatible interval shim ([#12](https://github.com/giocaizzi/greenhouse/issues/12)) ([9df3efc](https://github.com/giocaizzi/greenhouse/commit/9df3efc7ec1361a4d540e7dacd121f8b5b14c133))

## 1.0.0 (2026-05-11)

Initial release.

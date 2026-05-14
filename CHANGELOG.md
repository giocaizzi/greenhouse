# Changelog

All notable changes to this project are tracked here. Managed automatically by
[release-please](https://github.com/googleapis/release-please) from
[Conventional Commits](https://www.conventionalcommits.org/) — do not hand-edit.

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

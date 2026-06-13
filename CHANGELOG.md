# Changelog

All notable changes to this project are tracked here. Managed automatically by
[release-please](https://github.com/googleapis/release-please) from
[Conventional Commits](https://www.conventionalcommits.org/) — do not hand-edit.

## [3.0.0](https://github.com/giocaizzi/greenhouse/compare/v2.1.0...v3.0.0) (2026-06-13)


### ⚠ BREAKING CHANGES

* **engine,web,cli:** `IrrigationConfig.mode` and `auto_run` are now nullable (null = inherit from global). Existing rows are unaffected by the migration; new rows from the partial-PATCH config endpoint may omit fields. The legacy /clusters/{id}/{plants,sensors,irrigators,config} URLs return 301 redirects to the unified detail page anchors instead of rendering their own pages; old templates removed.
* **devices:** wire DeviceRegistry through services; remove TuyaDeviceManager shim ([#43](https://github.com/giocaizzi/greenhouse/issues/43))
* **api:** every /api/v1 route (except /api/v1/auth/login) and every web page now requires a valid session. Set GREENHOUSE_AUTH_SECRET_KEY plus GREENHOUSE_AUTH_ADMIN_USERNAME / _PASSWORD to bootstrap an admin on first run, or set IRRIGATION_AUTH_ENABLED=false to opt out (dev/migration only). MCP keeps its existing bearer token — these two auth surfaces stay isolated.
* **web:** every /api/v1 route (except /api/v1/auth/login) and every web page now requires a valid session. Set GREENHOUSE_AUTH_SECRET_KEY plus GREENHOUSE_AUTH_ADMIN_USERNAME / _PASSWORD to bootstrap an admin on first run, or set IRRIGATION_AUTH_ENABLED=false to opt out (dev/migration only). MCP keeps its existing bearer token — these two auth surfaces stay isolated.
* **cli:** every /api/v1 route (except /api/v1/auth/login) and every web page now requires a valid session. Set GREENHOUSE_AUTH_SECRET_KEY plus GREENHOUSE_AUTH_ADMIN_USERNAME / _PASSWORD to bootstrap an admin on first run, or set IRRIGATION_AUTH_ENABLED=false to opt out (dev/migration only). MCP keeps its existing bearer token — these two auth surfaces stay isolated.
* **engine:** every /api/v1 route (except /api/v1/auth/login) and every web page now requires a valid session. Set GREENHOUSE_AUTH_SECRET_KEY plus GREENHOUSE_AUTH_ADMIN_USERNAME / _PASSWORD to bootstrap an admin on first run, or set IRRIGATION_AUTH_ENABLED=false to opt out (dev/migration only). MCP keeps its existing bearer token — these two auth surfaces stay isolated.
* **sensors:** every /api/v1 route (except /api/v1/auth/login) and every web page now requires a valid session. Set GREENHOUSE_AUTH_SECRET_KEY plus GREENHOUSE_AUTH_ADMIN_USERNAME / _PASSWORD to bootstrap an admin on first run, or set IRRIGATION_AUTH_ENABLED=false to opt out (dev/migration only). MCP keeps its existing bearer token — these two auth surfaces stay isolated.
* **auth:** every /api/v1 route (except /api/v1/auth/login) and every web page now requires a valid session. Set GREENHOUSE_AUTH_SECRET_KEY plus GREENHOUSE_AUTH_ADMIN_USERNAME / _PASSWORD to bootstrap an admin on first run, or set IRRIGATION_AUTH_ENABLED=false to opt out (dev/migration only). MCP keeps its existing bearer token — these two auth surfaces stay isolated.

### Added

* **api:** close CRUD/pagination asymmetries + README drift ([#31](https://github.com/giocaizzi/greenhouse/issues/31)) ([f9d5333](https://github.com/giocaizzi/greenhouse/commit/f9d53334127a09ea9164b6c7cca87a7ef503188a))
* **auth:** add username/password authentication to API and web UI ([#27](https://github.com/giocaizzi/greenhouse/issues/27)) ([37eb5f1](https://github.com/giocaizzi/greenhouse/commit/37eb5f17a192db15a18850c4c7118816a98c8157))
* **cli:** add 'greenhouse stop-all' emergency kill switch ([#34](https://github.com/giocaizzi/greenhouse/issues/34)) ([24836c2](https://github.com/giocaizzi/greenhouse/commit/24836c268f9aeb86a786253663b8747cfb92bf27))
* **cli:** close API/CLI gaps ([#30](https://github.com/giocaizzi/greenhouse/issues/30)) ([e0343f1](https://github.com/giocaizzi/greenhouse/commit/e0343f17fd3addd940736cc8f4303211dcdb21e1))
* **devices:** unified DeviceHealthState + DeviceHealthMonitor across all adapters ([#42](https://github.com/giocaizzi/greenhouse/issues/42)) ([39d8eea](https://github.com/giocaizzi/greenhouse/commit/39d8eea7b0f69cf9eff4f2e178d900dde59d3cef))
* **engine,web,cli:** quiet-hours rule + hierarchical config + unified cluster view ([#57](https://github.com/giocaizzi/greenhouse/issues/57)) ([0ab9323](https://github.com/giocaizzi/greenhouse/commit/0ab9323832c711c928d312cafd9983cad5d76c51))
* **engine:** irrigation windows + seasonal frequency multiplier ([#29](https://github.com/giocaizzi/greenhouse/issues/29)) ([9293c24](https://github.com/giocaizzi/greenhouse/commit/9293c24c35089ab145b151537d53f1c8bfe3d637))
* **engine:** plant DB timing fields wired end-to-end + integration tests ([#38](https://github.com/giocaizzi/greenhouse/issues/38)) ([f17a31e](https://github.com/giocaizzi/greenhouse/commit/f17a31ee2e71b423cf8ae88aa27b6c523c612925))
* **notify:** ntfy push notifications for irrigation + alerts ([#58](https://github.com/giocaizzi/greenhouse/issues/58)) ([fc2ec09](https://github.com/giocaizzi/greenhouse/commit/fc2ec09593cc9312d4b6eadacbed92b980ee69a1))
* **plants:** add preferred watering hours + seasonal frequency multipliers per species/category ([#36](https://github.com/giocaizzi/greenhouse/issues/36)) ([b7c41f1](https://github.com/giocaizzi/greenhouse/commit/b7c41f1d6715ff556a01bcb6eed0165acae739a2))
* **pump:** detect empty reservoir via DP 105 and abort to protect pump ([#25](https://github.com/giocaizzi/greenhouse/issues/25)) ([2a06679](https://github.com/giocaizzi/greenhouse/commit/2a066791162157c658ec453ecf703c4d33a935fc))
* **sensors:** track sensor-plant assignment history ([#28](https://github.com/giocaizzi/greenhouse/issues/28)) ([52c24dd](https://github.com/giocaizzi/greenhouse/commit/52c24dd33c0f791f1b82d7b61ab208e28ff24a1b))
* **web:** close UX/CRUD gaps ([#32](https://github.com/giocaizzi/greenhouse/issues/32)) ([c4e6681](https://github.com/giocaizzi/greenhouse/commit/c4e66814b322a01c505279738fdca7e9561b0125))
* **web:** vacation edit + cluster irrigation windows UI ([#37](https://github.com/giocaizzi/greenhouse/issues/37)) ([5e785d9](https://github.com/giocaizzi/greenhouse/commit/5e785d9f5b8eae17ee8e3a939ae27e3a22d146f9))


### Fixed

* **auth:** accept MCP token in require_user so tool invocation works end-to-end ([#35](https://github.com/giocaizzi/greenhouse/issues/35)) ([4608966](https://github.com/giocaizzi/greenhouse/commit/4608966bbe6ded7bcfb8b2d800555d55bf494888))
* **engine:** seasonal multiplier was inverting interval direction ([#39](https://github.com/giocaizzi/greenhouse/issues/39)) ([ad5e21e](https://github.com/giocaizzi/greenhouse/commit/ad5e21edc556bd1888cb2775a7d9e3473451daaf))
* **tests:** make test_logic.py deterministic against irrigation-window gate ([#41](https://github.com/giocaizzi/greenhouse/issues/41)) ([fecdfa7](https://github.com/giocaizzi/greenhouse/commit/fecdfa7e68e20b3a721d66049edb0580ace60bf9))


### Changed

* **devices:** introduce DeviceRegistry + per-model adapters (no-op refactor) ([#40](https://github.com/giocaizzi/greenhouse/issues/40)) ([83fb233](https://github.com/giocaizzi/greenhouse/commit/83fb233c34294e69612f478c75e32038fe3e3c32))
* **devices:** wire DeviceRegistry through services; remove TuyaDeviceManager shim ([#43](https://github.com/giocaizzi/greenhouse/issues/43)) ([7fc303b](https://github.com/giocaizzi/greenhouse/commit/7fc303b79aeb4ec7a0c8e1ca50f30c0dbbf0a14d))

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

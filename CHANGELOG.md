# Changelog

All notable changes to this project are tracked here. Managed automatically by
[release-please](https://github.com/googleapis/release-please) from
[Conventional Commits](https://www.conventionalcommits.org/) — do not hand-edit.

## [6.6.1](https://github.com/giocaizzi/greenhouse/compare/v6.6.0...v6.6.1) (2026-10-16)


### Fixed

* **docker:** drop pip and apply debian updates in the runtime image ([#220](https://github.com/giocaizzi/greenhouse/issues/220)) ([a3809b9](https://github.com/giocaizzi/greenhouse/commit/a3809b92218e703a305c6740a36f533cc26d9d97))

## [6.6.0](https://github.com/giocaizzi/greenhouse/compare/v6.5.0...v6.6.0) (2026-10-16)


### Added

* add the repo's mcp servers ([#214](https://github.com/giocaizzi/greenhouse/issues/214)) ([2fedf07](https://github.com/giocaizzi/greenhouse/commit/2fedf078f12a1c437f81667c13acc8d888442868))


### Fixed

* **web:** keep chart card headings inside the card ([#218](https://github.com/giocaizzi/greenhouse/issues/218)) ([dffeccb](https://github.com/giocaizzi/greenhouse/commit/dffeccbab9cc3f0defb13c8ed677040b3379a985))

## [6.5.0](https://github.com/giocaizzi/greenhouse/compare/v6.4.0...v6.5.0) (2026-10-16)


### Added

* **charts:** hide unassigned gaps of sensors that served a plant ([#212](https://github.com/giocaizzi/greenhouse/issues/212)) ([dba6f83](https://github.com/giocaizzi/greenhouse/commit/dba6f83155def668163d82bd53201e0895c2ed77))
* **web:** cluster page health trajectories and per-plant snapshots ([#208](https://github.com/giocaizzi/greenhouse/issues/208)) ([d3d4d0b](https://github.com/giocaizzi/greenhouse/commit/d3d4d0b2b6d9c1d8ca24064000b57f9b1363babd))
* **web:** drop Today from the topbar, name the brand Greenhouse, align the nav ([#205](https://github.com/giocaizzi/greenhouse/issues/205)) ([437ef2e](https://github.com/giocaizzi/greenhouse/commit/437ef2eb4889c1ba443b183a8079664adf48ef3c))
* **web:** open the alert inbox on open alerts ([#203](https://github.com/giocaizzi/greenhouse/issues/203)) ([385ed9b](https://github.com/giocaizzi/greenhouse/commit/385ed9be344043db223236bbd26f2e4674498b2f))
* **web:** soil range strip on the home cluster cards ([#207](https://github.com/giocaizzi/greenhouse/issues/207)) ([67f2343](https://github.com/giocaizzi/greenhouse/commit/67f234389f1e4216345ceebf9ac901452a7155a0))


### Fixed

* **web:** show the inherited config value wherever a field is unset ([#204](https://github.com/giocaizzi/greenhouse/issues/204)) ([ca0593e](https://github.com/giocaizzi/greenhouse/commit/ca0593ed4d1a4afd01c16c5d407074dc28338eea))

## [6.4.0](https://github.com/giocaizzi/greenhouse/compare/v6.3.0...v6.4.0) (2026-10-15)


### Added

* **alerts:** daily ntfy digest of critical alerts still open ([#188](https://github.com/giocaizzi/greenhouse/issues/188)) ([811ca28](https://github.com/giocaizzi/greenhouse/commit/811ca287ddda25c57c5f20bbcd5560ed13d652a9))
* **engine:** show sensor_flatline and the soil band source in the decision trail ([#191](https://github.com/giocaizzi/greenhouse/issues/191)) ([806aae7](https://github.com/giocaizzi/greenhouse/commit/806aae71287cec272a3b9f07d53842d00688e132))
* **plants:** per-plant soil_moisture_target override ([#196](https://github.com/giocaizzi/greenhouse/issues/196)) ([e63f07b](https://github.com/giocaizzi/greenhouse/commit/e63f07b8215faefd0298ecfda52c80ca6a60561c))
* **system-health:** report an irrigator offline from the health monitor ([#192](https://github.com/giocaizzi/greenhouse/issues/192)) ([0432dd1](https://github.com/giocaizzi/greenhouse/commit/0432dd1f30cd1ff8d4ffb0d7252986063076f1e1))
* **web:** move, restore and purge sensors and plants from the web UI and CLI ([#195](https://github.com/giocaizzi/greenhouse/issues/195)) ([9709245](https://github.com/giocaizzi/greenhouse/commit/97092458de38e3cee96d5eece5e5d037b7e1de45))
* **web:** use a colour-blind-safe chart palette ([#190](https://github.com/giocaizzi/greenhouse/issues/190)) ([edcce7b](https://github.com/giocaizzi/greenhouse/commit/edcce7b55e542c00528893e9be7b38d81ad5eb17))


### Fixed

* **alerts:** move a plant's open alerts with the plant ([#186](https://github.com/giocaizzi/greenhouse/issues/186)) ([49d84f9](https://github.com/giocaizzi/greenhouse/commit/49d84f919fd925733fc35ccf75bacc0e06f776c4))
* **efficacy:** attribute readings by sensor assignment in the efficacy score ([#193](https://github.com/giocaizzi/greenhouse/issues/193)) ([dd4468d](https://github.com/giocaizzi/greenhouse/commit/dd4468dfdfab7286a4eb83ae6d8ce2480420acc5))


### Performance

* **charts:** bucket readings hourly for chart ranges beyond 14 days ([#194](https://github.com/giocaizzi/greenhouse/issues/194)) ([20f181a](https://github.com/giocaizzi/greenhouse/commit/20f181ae150e301971040e4e8c74da45517ce444))

## [6.3.0](https://github.com/giocaizzi/greenhouse/compare/v6.2.0...v6.3.0) (2026-10-15)


### Added

* **web:** plant health chart on the cluster and plant pages ([#176](https://github.com/giocaizzi/greenhouse/issues/176)) ([0a9fed7](https://github.com/giocaizzi/greenhouse/commit/0a9fed7472ea5efa2c8de524f7e75d5aa7867548))


### Fixed

* **alerts:** name the plant and cluster in sensor alerts and irrigation pushes ([#172](https://github.com/giocaizzi/greenhouse/issues/172)) ([b086335](https://github.com/giocaizzi/greenhouse/commit/b086335da027d651fd3487b7ea92fed630560343))
* **anomaly:** resolve sensor_drift once the latest reading is back in range ([#170](https://github.com/giocaizzi/greenhouse/issues/170)) ([fd6c76e](https://github.com/giocaizzi/greenhouse/commit/fd6c76e1f55b6aa9bed11d292b5afe1f5f0fd5d6))
* **api:** mask the irrigator device_ip in responses like the local key ([#175](https://github.com/giocaizzi/greenhouse/issues/175)) ([75cfe6b](https://github.com/giocaizzi/greenhouse/commit/75cfe6b011e3d04404f6e011f38a1360ebbf9594))
* **notify:** keep emoji and accents in ntfy push titles ([#174](https://github.com/giocaizzi/greenhouse/issues/174)) ([4742adc](https://github.com/giocaizzi/greenhouse/commit/4742adc0fbe4b9bbe234815caa01811b36dc341c))
* **web:** title the plant page with the species, not the notes ([#173](https://github.com/giocaizzi/greenhouse/issues/173)) ([dc47420](https://github.com/giocaizzi/greenhouse/commit/dc47420fa656e40847ff4590f59724c9e9fe6422))

## [6.2.0](https://github.com/giocaizzi/greenhouse/compare/v6.1.0...v6.2.0) (2026-10-15)


### Added

* **alerts:** re-notify warning and critical alerts that stay open ([#158](https://github.com/giocaizzi/greenhouse/issues/158)) ([78f099c](https://github.com/giocaizzi/greenhouse/commit/78f099c0a61f145554d966938668cbeb19b43443))
* **anomaly:** flag a soil sensor flat-lined at 0 % or 100 % as sensor_flatline ([#157](https://github.com/giocaizzi/greenhouse/issues/157)) ([a8643c7](https://github.com/giocaizzi/greenhouse/commit/a8643c7eeb75284a2bca877e9b4d31287bf76787))
* **charts:** label chart series by the plant the sensor was measuring ([#167](https://github.com/giocaizzi/greenhouse/issues/167)) ([2ff7856](https://github.com/giocaizzi/greenhouse/commit/2ff7856da42bb7dd21de6660f7dccf5c8ea5aa4b))
* **charts:** offer chart timeframes in days (3, 14, 30, 60, 90) ([#163](https://github.com/giocaizzi/greenhouse/issues/163)) ([6baf834](https://github.com/giocaizzi/greenhouse/commit/6baf83486fa8896a0e909d1dd0f82cf685bfc4d2))
* **inventory:** archive instead of hard-deleting sensors and plants ([#165](https://github.com/giocaizzi/greenhouse/issues/165)) ([963417d](https://github.com/giocaizzi/greenhouse/commit/963417ddb9307ec51381c99613afc46619381601))
* **monitor:** alert and push when a sensor-only cluster needs water ([#164](https://github.com/giocaizzi/greenhouse/issues/164)) ([c589c11](https://github.com/giocaizzi/greenhouse/commit/c589c114d0ceb7254a861f8bdaa397980a1f4827))
* **plants:** flag plants that run on the default soil band ([#160](https://github.com/giocaizzi/greenhouse/issues/160)) ([d2c85b6](https://github.com/giocaizzi/greenhouse/commit/d2c85b6714c37d645f6fa164232b940993c27f6c))


### Fixed

* **anomaly:** treat a rising step that holds as a watering, not sensor_drift ([#153](https://github.com/giocaizzi/greenhouse/issues/153)) ([6fdbe91](https://github.com/giocaizzi/greenhouse/commit/6fdbe91144aae725275c245b8ba365e6ac58e312))
* **plant-db:** resolve categories case-insensitively and add Ficus benjamina ([#155](https://github.com/giocaizzi/greenhouse/issues/155)) ([56016b5](https://github.com/giocaizzi/greenhouse/commit/56016b55b104cec6bdb5cb305883fd52343e0e43))
* **sensors:** attribute readings by assignment window everywhere ([#159](https://github.com/giocaizzi/greenhouse/issues/159)) ([4e72b70](https://github.com/giocaizzi/greenhouse/commit/4e72b70bc19426b8fe30009ae4d8940db51678cb))
* **system-health:** mark an irrigator failing after repeated failed starts ([#161](https://github.com/giocaizzi/greenhouse/issues/161)) ([3bbfca9](https://github.com/giocaizzi/greenhouse/commit/3bbfca936be02e8fcf654030b3946946115456e7))

## [6.1.0](https://github.com/giocaizzi/greenhouse/compare/v6.0.0...v6.1.0) (2026-10-13)


### Added

* **alerts:** raise a critical alert when the Tuya subscription expires ([#138](https://github.com/giocaizzi/greenhouse/issues/138)) ([be98cce](https://github.com/giocaizzi/greenhouse/commit/be98ccea63e09606e1f782a7f252c6ed4f8d450a))


### Fixed

* **anomaly:** judge sensor_stale against the sync interval and auto-resolve it ([#140](https://github.com/giocaizzi/greenhouse/issues/140)) ([5031c6e](https://github.com/giocaizzi/greenhouse/commit/5031c6ea8f9b6402a78e0f383cc3296722c58d42))

## [6.0.0](https://github.com/giocaizzi/greenhouse/compare/v5.0.1...v6.0.0) (2026-10-12)


### ⚠ BREAKING CHANGES

* secrets are read only as `GREENHOUSE_*` (bare and `IRRIGATION_`-prefixed names are ignored); the server binds `127.0.0.1` unless `IRRIGATION_HOST` is set; `local_key` is masked in API and MCP responses; `IRRIGATION_CHECK_INTERVAL_HOURS` is gone (use `IRRIGATION_CHECK_CRON_HOURS=*/N`); `dry_run_global` refuses manual starts with 409; pre-Alembic databases fail at startup and need `alembic stamp head`.

### Added

* **cli:** add full-screen TUI (greenhouse tui) + scheduler, engine and manual-control fixes ([#106](https://github.com/giocaizzi/greenhouse/issues/106)) ([a1b2622](https://github.com/giocaizzi/greenhouse/commit/a1b26221ad157454ce8aa35619afeb047b06dd21))


### Fixed

* **project:** set package author to the owner ([#116](https://github.com/giocaizzi/greenhouse/issues/116)) ([d7efe7c](https://github.com/giocaizzi/greenhouse/commit/d7efe7c1dec0e2bf1623c63cf81be25c069822ed))


### Changed

* restructure the codebase, fix every recorded bug, enable all ruff rules and type-check tests ([#108](https://github.com/giocaizzi/greenhouse/issues/108)) ([0a83268](https://github.com/giocaizzi/greenhouse/commit/0a83268afeb9a8dac07adba605811f684f7d8562))

## [5.0.1](https://github.com/giocaizzi/greenhouse/compare/v5.0.0...v5.0.1) (2026-08-18)


### Fixed

* **leak:** stop false-positive leak alerts and make the 24h hold real ([#104](https://github.com/giocaizzi/greenhouse/issues/104)) ([de4bae8](https://github.com/giocaizzi/greenhouse/commit/de4bae801638068365fab0f6c33642483be4afc8)), closes [#103](https://github.com/giocaizzi/greenhouse/issues/103)

## [5.0.0](https://github.com/giocaizzi/greenhouse/compare/v4.0.0...v5.0.0) (2026-07-20)


### ⚠ BREAKING CHANGES

* **devices:** greenhouse_core.TuyaCloud and greenhouse_core.devices.tuya_transport.TuyaTransport are removed — use greenhouse_core.devices.DeviceGateway. AbstractSensorAdapter.read_health now takes an optional `latest` SensorReading. SyncService.sync_and_read_sensors is renamed to ensure_fresh_and_read.

### Performance

* **devices:** unify Tuya access behind DeviceGateway and cut IoT Core calls ([#95](https://github.com/giocaizzi/greenhouse/issues/95)) ([70b35d4](https://github.com/giocaizzi/greenhouse/commit/70b35d411ff6138a85f5e7137f4a3b284e390f29))

## [4.0.0](https://github.com/giocaizzi/greenhouse/compare/v3.1.0...v4.0.0) (2026-06-22)


### ⚠ BREAKING CHANGES

* per-cluster irrigator CRUD is now the singular resource /clusters/{id}/irrigator (POST 409 / GET-PUT-DELETE 404); cluster detail/status expose a single `irrigator | null`; CLI irrigator update/delete are cluster-keyed and `irrigator show` replaces per-cluster list. Global /irrigators and device-action endpoints are unchanged.

### Added

* **logic:** clean noisy sensor series before decisions ([#90](https://github.com/giocaizzi/greenhouse/issues/90)) ([c934079](https://github.com/giocaizzi/greenhouse/commit/c9340790bb69c4a24dbf84ba5a529aa4190b3228))
* vacation reservoir rationing + strict 0:1 cluster↔irrigator ([#72](https://github.com/giocaizzi/greenhouse/issues/72)) ([79157fc](https://github.com/giocaizzi/greenhouse/commit/79157fc127eaae332ebca13dac4581c85104394e))
* **web:** gate UI by cluster capability and add settings/mobile/a11y polish ([#80](https://github.com/giocaizzi/greenhouse/issues/80)) ([cc3be7f](https://github.com/giocaizzi/greenhouse/commit/cc3be7f977c21e8ed33863fe4b3beae15fd78b82))


### Fixed

* **engine:** water any hour when no windows are configured ([#83](https://github.com/giocaizzi/greenhouse/issues/83)) + engine hardening ([#84](https://github.com/giocaizzi/greenhouse/issues/84)) ([e7f7525](https://github.com/giocaizzi/greenhouse/commit/e7f7525bee8e39e0057f0777a980ad7bcd5f96c1))
* **scheduler:** make UserPreferences.timezone the single source of truth ([#85](https://github.com/giocaizzi/greenhouse/issues/85)) ([f0d4e8b](https://github.com/giocaizzi/greenhouse/commit/f0d4e8bdee0b094876c22c244eec3d4bcfa087a7))
* **web:** stop closed command palette from blocking mobile taps ([#76](https://github.com/giocaizzi/greenhouse/issues/76)) ([2e239d0](https://github.com/giocaizzi/greenhouse/commit/2e239d09c327d9344bdded7e8edf01d55faabd54))

## [3.1.0](https://github.com/giocaizzi/greenhouse/compare/v3.0.2...v3.1.0) (2026-06-14)


### Added

* **web:** redesign UI for clarity, safety, and accessibility ([#70](https://github.com/giocaizzi/greenhouse/issues/70)) ([f38e659](https://github.com/giocaizzi/greenhouse/commit/f38e659e85474f02c79ba55ff7d316032f4a1076))

## [3.0.2](https://github.com/giocaizzi/greenhouse/compare/v3.0.1...v3.0.2) (2026-06-13)


### Fixed

* **mcp:** shorten tool names to pass Claude.ai 64-char limit ([#66](https://github.com/giocaizzi/greenhouse/issues/66)) ([e62d4ab](https://github.com/giocaizzi/greenhouse/commit/e62d4abbe0246f5316c84eaf40747d49c67e0b59)), closes [#62](https://github.com/giocaizzi/greenhouse/issues/62)

## [3.0.1](https://github.com/giocaizzi/greenhouse/compare/v3.0.0...v3.0.1) (2026-06-13)


### Fixed

* **web:** stop login page infinite-render loop under session auth ([#63](https://github.com/giocaizzi/greenhouse/issues/63)) ([8e0e17b](https://github.com/giocaizzi/greenhouse/commit/8e0e17b4d864bb18b21b753248a245756ebc151b))

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

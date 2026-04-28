# API Reference

Server runs at `http://localhost:8000` by default. Interactive docs at `/docs`.
All endpoints are under `/api/v1`. One line per endpoint: `METHOD /path — purpose`.

## clusters

```
POST   /api/v1/clusters                                      — Create a cluster
GET    /api/v1/clusters                                      — List all clusters
GET    /api/v1/clusters/{cluster_id}                         — Get a cluster by ID
PUT    /api/v1/clusters/{cluster_id}                         — Update a cluster
DELETE /api/v1/clusters/{cluster_id}                         — Delete a cluster
```

## plants

```
POST   /api/v1/clusters/{cluster_id}/plants                  — Add a plant to a cluster
GET    /api/v1/clusters/{cluster_id}/plants                  — List plants in a cluster
PUT    /api/v1/clusters/{cluster_id}/plants/{plant_id}       — Update a plant
DELETE /api/v1/clusters/{cluster_id}/plants/{plant_id}       — Delete a plant
POST   /api/v1/plants/sync                                   — Sync plant care data from plant_database.json
GET    /api/v1/plants/{plant_id}/health                      — Get daily 0–100 health score for a plant
POST   /api/v1/plants/health/snapshot                        — Trigger a health snapshot for all plants
```

## irrigators

```
POST   /api/v1/clusters/{cluster_id}/irrigators              — Add an irrigator to a cluster
GET    /api/v1/clusters/{cluster_id}/irrigators              — List irrigators in a cluster
GET    /api/v1/clusters/{cluster_id}/irrigators/{id}         — Get an irrigator by ID
PUT    /api/v1/clusters/{cluster_id}/irrigators/{id}         — Update an irrigator
DELETE /api/v1/clusters/{cluster_id}/irrigators/{id}         — Delete an irrigator
POST   /api/v1/irrigators/{irrigator_id}/start               — Start an irrigator for a given duration
POST   /api/v1/irrigators/{irrigator_id}/stop                — Stop an irrigator immediately
POST   /api/v1/irrigators/{irrigator_id}/log-manual          — Log a manual irrigation event
```

## sensors

```
POST   /api/v1/clusters/{cluster_id}/sensors                 — Add a sensor to a cluster
GET    /api/v1/clusters/{cluster_id}/sensors                 — List sensors in a cluster
GET    /api/v1/clusters/{cluster_id}/sensors/{sensor_id}     — Get a sensor by ID
PUT    /api/v1/clusters/{cluster_id}/sensors/{sensor_id}     — Update a sensor
DELETE /api/v1/clusters/{cluster_id}/sensors/{sensor_id}     — Delete a sensor
```

## configs

```
PUT    /api/v1/clusters/{cluster_id}/config                  — Set irrigation config for a cluster
GET    /api/v1/clusters/{cluster_id}/config                  — Get irrigation config for a cluster
```

## operations

```
GET    /api/v1/clusters/{cluster_id}/status                  — Full cluster status with latest decision
POST   /api/v1/clusters/{cluster_id}/irrigate                — Run smart irrigation pipeline
GET    /api/v1/clusters/{cluster_id}/monitor                 — Moisture monitoring (no irrigation)
POST   /api/v1/check                                         — Check all clusters (sync + decide + alerts)
POST   /api/v1/clusters/{cluster_id}/check                   — Check a single cluster
POST   /api/v1/sync                                          — Sync sensor data from Tuya Cloud
GET    /api/v1/clusters/{cluster_id}/learn                   — Learning report (absorption, drainage, alerts)
GET    /api/v1/clusters/{cluster_id}/history                 — Readings and irrigation events
GET    /api/v1/clusters/{cluster_id}/stats                   — Irrigation statistics
GET    /api/v1/clusters/{cluster_id}/stats/export            — CSV export of statistics (binary)
GET    /api/v1/clusters/{cluster_id}/forecast                — Next-irrigation forecast with weather context
GET    /api/v1/clusters/{cluster_id}/insights                — Aggregated signal insight cards for a cluster
GET    /api/v1/clusters/{cluster_id}/efficacy                — Irrigation efficacy scores per cycle
GET    /api/v1/quality/report                                — Data quality report (coverage + anomalies per sensor)
```

## scheduler

```
GET    /api/v1/health                                        — Server health and scheduler status
GET    /api/v1/health/system                                 — System health pulse (DB, scheduler, devices)
GET    /api/v1/scheduler/jobs                                — List scheduled background jobs
DELETE /api/v1/scheduler/jobs/{job_id}                       — Remove a scheduled job
```

## charts

```
GET    /api/v1/plants/{plant_id}                             — Get plant detail (charts router)
GET    /api/v1/plants/{plant_id}/chart-data                  — Sensor chart data for a plant
GET    /api/v1/clusters/{cluster_id}/chart-data              — Sensor chart data for a cluster
```

## alerts

```
GET    /api/v1/alerts                                        — List alert inbox (filter by status, severity, cluster)
GET    /api/v1/alerts/{alert_id}                             — Get a single alert by ID
POST   /api/v1/alerts/{alert_id}/acknowledge                 — Move alert to acknowledged state
POST   /api/v1/alerts/{alert_id}/resolve                     — Mark alert as resolved
POST   /api/v1/clusters/{cluster_id}/alerts/sync             — Recompute and reconcile alerts for a cluster
POST   /api/v1/alerts/sync                                   — Recompute and reconcile alerts for all clusters
```

## activity

```
GET    /api/v1/activity                                      — Cross-cutting activity timeline (all entity types)
```

## decisions

```
GET    /api/v1/clusters/{cluster_id}/decisions               — Irrigation decision audit log for a cluster
```

## preferences

```
GET    /api/v1/preferences                                   — Get user preferences (units, timezone, theme, dry-run)
PUT    /api/v1/preferences                                   — Update user preferences
```

## vacation

```
GET    /api/v1/vacation                                      — List vacation windows
POST   /api/v1/vacation                                      — Create a vacation window (pause irrigation)
DELETE /api/v1/vacation/{window_id}                          — Delete a vacation window
```

## search

```
GET    /api/v1/search                                        — Global search across clusters, plants, sensors, irrigators
```

## bulk

```
POST   /api/v1/bulk/stop-all                                 — Emergency stop all irrigators
```

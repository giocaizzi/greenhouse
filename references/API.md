# API Reference

Server runs at `http://localhost:8000` by default. Interactive docs at `/docs`.

## CRUD Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/clusters` | Create cluster |
| GET | `/api/v1/clusters` | List clusters |
| GET | `/api/v1/clusters/{id}` | Get cluster |
| POST | `/api/v1/clusters/{id}/plants` | Add plant |
| GET | `/api/v1/clusters/{id}/plants` | List plants |
| POST | `/api/v1/plants/sync` | Sync plant care data |
| POST | `/api/v1/clusters/{id}/irrigators` | Add irrigator |
| GET | `/api/v1/clusters/{id}/irrigators` | List irrigators |
| POST | `/api/v1/irrigators/{id}/start` | Start irrigator |
| POST | `/api/v1/irrigators/{id}/stop` | Stop irrigator |
| POST | `/api/v1/irrigators/{id}/log-manual` | Log manual irrigation |
| POST | `/api/v1/clusters/{id}/sensors` | Add sensor |
| GET | `/api/v1/clusters/{id}/sensors` | List sensors |
| PUT | `/api/v1/clusters/{id}/config` | Set config |
| GET | `/api/v1/clusters/{id}/config` | Get config |

## Operation Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/clusters/{id}/status` | Full cluster status with decision |
| POST | `/api/v1/clusters/{id}/irrigate` | Smart irrigation pipeline |
| GET | `/api/v1/clusters/{id}/monitor` | Moisture monitoring |
| POST | `/api/v1/check` | Check all clusters |
| POST | `/api/v1/clusters/{id}/check` | Check single cluster |
| POST | `/api/v1/sync` | Sync all sensor data |
| GET | `/api/v1/clusters/{id}/learn` | Learning report |
| GET | `/api/v1/clusters/{id}/history` | Readings + events |
| GET | `/api/v1/clusters/{id}/stats` | Statistics |
| GET | `/api/v1/clusters/{id}/stats/export` | CSV export |
| GET | `/api/v1/health` | Server health + scheduler |
| GET | `/api/v1/scheduler/jobs` | List scheduled jobs |
| DELETE | `/api/v1/scheduler/jobs/{id}` | Remove a job |

## Key Request/Response Schemas

### Create Cluster
```json
POST /api/v1/clusters
{"name": "My Plants", "location": "Indoor", "environment": "indoor"}
→ {"id": 1, "name": "My Plants", "location": "Indoor", "created_at": 1234567890, "environment": "indoor"}
```

### Irrigate
```json
POST /api/v1/clusters/1/irrigate
{"temp_override": null, "dry_run": false, "no_sync": false}
→ {"action": "irrigate|skip|error", "reason": "...", "confidence": 0.85, "duration_minutes": 2, "interval_hours": 12}
```

### Check All
```json
POST /api/v1/check
→ {"results": [{"cluster_id": 1, "cluster_name": "...", "action": "irrigated|skipped|monitored", "alerts": [...], "maintenance": [...]}], "has_alerts": false}
```

### Sync
```json
POST /api/v1/sync
{"hours": 24}
→ {"total_synced": 10, "total_new": 3, "total_live": 1, "errors": []}
```

### Health
```json
GET /api/v1/health
→ {"status": "ok", "scheduler_running": true, "jobs": [{"id": "sensor_sync", "name": "Sensor data sync", "trigger": "interval[0:30:00]", "next_run_time": "..."}]}
```

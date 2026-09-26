# API 接口规范

> 本文记录现有 `/api` 旧接口，供渐进式重构期间兼容使用。新版状态、权限、
> ID、REST v3 和 SSE 契约见
> [`docs/rebuild/state-and-api-contract.md`](rebuild/state-and-api-contract.md)。
> 未列入 v3 契约的旧接口不得继续扩展；普通 user 不再访问全社区旧接口。

## 基础信息
- Base URL: `http://localhost:5000/api`
- 数据格式: JSON
- 编码: UTF-8

---

## 1. 系统

### GET /api/health
系统健康检查

**Response**
```json
{ "status": "ok", "model_loaded": true, "uptime": 3600 }
```

---

## 2. 仪表盘

### GET /api/dashboard/stats
获取仪表盘统计数据

**Response**
```json
{
  "total_scanned": 128500,
  "alerts_today": 23,
  "active_threats": 5,
  "system_status": "normal",
  "traffic_history": [
    { "time": "14:00", "normal": 1200, "attack": 45 },
    { "time": "14:05", "normal": 1180, "attack": 32 }
  ],
  "attack_distribution": [
    { "type": "Mirai", "count": 12 },
    { "type": "Gafgyt", "count": 8 }
  ],
  "recent_alerts": [...]
}
```

---

## 3. 检测

### POST /api/detect/upload
上传 PCAP 文件进行离线检测

**Request**: `multipart/form-data` (field: `file`)

**Response**
```json
{
  "job_id": "uuid",
  "total_packets": 5000,
  "results": [
    {
      "timestamp": "2026-07-14T14:30:00",
      "src_ip": "192.168.1.105",
      "dst_ip": "192.168.1.1",
      "attack_type": "Mirai",
      "confidence": 0.97,
      "risk_level": "critical"
    }
  ]
}
```

### POST /api/detect/realtime/start
开启实时检测

### POST /api/detect/realtime/stop
停止实时检测

---

## 4. 告警

### GET /api/alerts
获取告警列表

**Params**
| 参数 | 类型 | 说明 |
|------|------|------|
| page | int | 页码 (default 1) |
| page_size | int | 每页条数 (default 20) |
| risk_level | string | 风险等级筛选 |
| attack_type | string | 攻击类型筛选 |
| start_time | string | 开始时间 |
| end_time | string | 结束时间 |
| src_ip | string | 源 IP 筛选 |
| merged | bool | 是否显示合并后告警 |

**Response**
```json
{
  "total": 156,
  "page": 1,
  "items": [
    {
      "id": 1,
      "risk_level": "critical",
      "attack_type": "Mirai",
      "src_ip": "192.168.1.105",
      "dst_ip": "192.168.1.1",
      "confidence": 0.97,
      "timestamp": "2026-07-14T14:30:00",
      "merged_count": 5,
      "status": "new",
      "description": "Mirai 僵尸网络扫描行为"
    }
  ]
}
```

### POST /api/alerts/:id/block
拉黑 IP

### POST /api/alerts/:id/trace
溯源分析

### POST /api/alerts/:id/false-positive
标记误报

### POST /api/alerts/merge
手动触发告警合并

---

## 5. 配置

### GET /api/config
获取当前配置

### PUT /api/config
更新配置

**Request Body**
```json
{
  "detection_mode": "offline",
  "confidence_threshold": 0.85,
  "merge_window_minutes": 5,
  "auto_block": false
}
```

---

## 6. 数据导出

### GET /api/export/excel
导出告警记录为 Excel

**Params**: 同 GET /api/alerts 的筛选参数

**Response**: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`


## 7. 系统健康

### GET /api/v3/system/health

只读运行状态快照。Web session 的 `admin` 与 `operator` 可读；匿名请求返回 401，普通 `user` 与 Mobile Bearer 返回 403。该请求不会建库、迁移、备份、清理、VACUUM 或访问外部服务。

响应不含数据库路径、凭据、token/hash、MAC/IP、异常堆栈或任意环境变量。`components` 中每项返回 `status`（`ready`、`warming_up`、`degraded`、`unavailable`）、更新时间及可选稳定 `reason_code`；数据库状态另含 schema、只读 integrity check 与容量信息。GNN/Graph capability 固定为 unavailable。

```json
{
  "observed_at": "2026-09-24T12:00:00Z",
  "automatic_maintenance": false,
  "components": {
    "api": {"status": "ready", "updated_at": "2026-09-24T12:00:00Z", "reason_code": null},
    "database": {"status": "ready", "exists": true, "readable": true, "writable": true, "updated_at": "2026-09-24T12:00:00Z", "reason_code": null},
    "schema": {"status": "ready", "version": 9, "legacy_schema_ready": true, "migration_complete": true, "migration_checksums_valid": true, "updated_at": "2026-09-24T12:00:00Z", "reason_code": null},
    "integrity_check": {"status": "ready", "result": "ok", "checked_at": "2026-09-24T12:00:00Z", "updated_at": "2026-09-24T12:00:00Z", "reason_code": null},
    "mqtt": {"status": "unavailable", "updated_at": "2026-09-24T12:00:00Z", "reason_code": "mqtt_disabled"},
    "traffic": {"status": "warming_up", "aggregation_status": "warming_up", "capture_status": "unavailable", "capture_reason_code": "capture_not_started", "updated_at": "2026-09-24T12:00:00Z", "reason_code": "traffic_not_started"},
    "event_log": {"status": "ready", "retained_events": 0, "oldest_event_id": null, "latest_event_id": null, "latest_cursor": 0, "updated_at": "2026-09-24T12:00:00Z", "reason_code": null},
    "incident": {"status": "ready", "updated_at": "2026-09-24T12:00:00Z", "reason_code": null},
    "mobile": {"status": "ready", "updated_at": "2026-09-24T12:00:00Z", "reason_code": null},
    "discovery": {"status": "ready", "updated_at": "2026-09-24T12:00:00Z", "reason_code": null},
    "graph": {"status": "unavailable", "updated_at": "2026-09-24T12:00:00Z", "reason_code": "graph_capability_unavailable"}
  },
  "maintenance": {"last_successful_at": null, "last_plan_at": null, "last_apply_at": null, "last_plan_reason_code": "maintenance_plan_read_only", "reason_code": "no_maintenance_run"},
  "capacity": {"database_file_bytes": 0, "page_size_bytes": 4096, "page_count": 0, "free_pages": 0, "free_bytes": 0, "disk_free_bytes": 0}
}
```

容量数值示意；实际缺失或不可读取时返回 `null`。实时事件清理或其他游标断档使连续性无法证明时，`GET /api/v3/events` 返回 `snapshot.required`，客户端应先拉取快照并从快照游标重新订阅。维护流程见 [运行维护与故障恢复](12-operations-and-recovery.md)。

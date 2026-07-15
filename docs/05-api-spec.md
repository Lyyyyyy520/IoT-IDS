# API 接口规范

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

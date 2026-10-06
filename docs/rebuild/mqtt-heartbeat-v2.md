# MQTT 心跳协议 v2 与 ingestion 边界

## 1. 本轮范围

该里程碑实现版本化消息信封、两套 ESP32 消息生成和可直接注入
`topic + payload + received_at` 的纯后端 ingestion。ingestion 继续与网络层分离；
后续增加的默认关闭订阅适配器见 `docs/rebuild/mqtt-subscriber.md`。

## 2. Topic 与 JSON 信封

发布 topic 必须严格为：

```text
community/{device_id}/status
```

正式消息示例：

```json
{
  "schema_version": 2,
  "device_id": "camera-01",
  "boot_id": "4f8c3d1670f24dc982a4e565e27f7810",
  "sequence": 17,
  "firmware_version": "0.3.0",
  "uptime_ms": 85000,
  "ip": "192.168.4.21",
  "mac": "AA:BB:CC:DD:EE:02",
  "telemetry": {
    "device_type": "camera",
    "state": "recording",
    "angle": 90
  }
}
```

旧固件的 `{"device": ..., "type": ..., "state": ...}` 没有 `schema_version`，
必须被当作不支持的旧格式拒绝，不能补字段后假装是 v2。

## 3. 字段规则

| 字段 | 规则 |
|---|---|
| `schema_version` | 整数且必须为 `2` |
| `device_id` | 2～64 位小写 topic-safe ID；必须与 topic 中 ID 完全相同 |
| `boot_id` | 每次启动生成新的 32 位小写十六进制随机值 |
| `sequence` | SQLite 有符号 64 位非负整数；同一 boot 严格递增 |
| `firmware_version` | 1～32 位受限版本字符串 |
| `uptime_ms` | 非负 64 位整数；同一 boot 不得倒退，只作设备证据 |
| `ip` | 当前运行时读取的有效单播 IPv4，不属于设备身份 |
| `mac` | 当前运行时读取的 6 字节单播 MAC；必须匹配已登记身份 |
| `telemetry` | 最大 2048 字节、最多四层嵌套的 JSON 对象 |
| `device_time` | 可选、带时区 ISO-8601；只作证据，不参与在线判定 |

整个 MQTT payload 最大 4096 字节，必须是严格 UTF-8 JSON 对象；重复 JSON key、
`NaN/Infinity`、未知 envelope 字段、错误类型和越界值都会被拒绝。

## 4. boot、sequence 与重放

- 首个 boot 或新的 boot 只接受 sequence `0` 或 `1`。
- 当前 boot 只接受大于最后已接收值的 sequence；相等为重复，小于为乱序。
- 新 boot 接受后会成为当前 boot，并允许 uptime 和 sequence 从初始值重新开始。
- 所有已见 boot 会话都会保留；当前 boot 切换后，再收到旧 boot 的任何 sequence
  都返回 `replayed_boot`，不能刷新状态。
- 会话游标、观测记录和当前设备状态在同一个数据库事务中提交。
- 重复、乱序、旧 boot 重放或任何校验失败都不能新增观测、更新
  `last_received_at` 或把离线设备恢复为在线。

## 5. 状态与时间边界

MQTT 回调接收消息时由后端生成带时区的 `received_at`，并把它注入
`MqttHeartbeatIngestor.ingest()`。该时间是 `online/stale/offline` 的唯一依据。

设备可选 `device_time` 会进入 `last_observed_at`，`uptime_ms` 会进入观测和 boot
会话表；两者都不能覆盖 `last_received_at`。服务端不能采用 payload 中的时间维持
在线状态。

## 6. 设备信任与认证边界

- Mosquitto 用户名、密码和 ACL 负责认证发布者，并限制设备只能发布自己的 topic。
- 常见 MQTT 客户端回调只提供 topic 和 payload，通常无法读取发布者用户名。
- 应用层能够验证 topic ID、payload ID、已登记 `device_id` 和绑定 MAC 的一致性，
  但这不能替代 Broker 认证或 ACL。
- 未登记 `device_id` 返回 `unknown_device`，不会自动创建可信设备或档案。
- ingestion 模块不会实例化 MQTT 网络客户端；订阅适配器默认关闭并要求显式启停。

## 7. 数据库 migration v2

version 1 的 SQL 和 checksum 保持不变。version 2 `mqtt_heartbeat_sessions`：

- 为 `v3_device_state_observations` 增加 `boot_id`、`firmware_version`、`uptime_ms`；
- 新增 `v3_mqtt_boot_sessions`，永久记录每台设备见过的 boot 和最后游标；
- 新增 `v3_mqtt_device_cursors`，记录当前 boot；
- 新增 `(device_id, boot_id, sequence)` 唯一索引和会话接收时间索引。

旧观测新增列允许为空，不重写或猜测历史 boot。migration v2 与版本登记处于同一
事务，可以重复运行；已登记版本会按 checksum 跳过。

## 8. 结构化结果

成功返回 `accepted`。拒绝结果使用稳定 code，包括：`invalid_topic`、
`invalid_utf8`、`invalid_json`、`payload_too_large`、`unsupported_schema_version`、
`device_id_mismatch`、`identity_mismatch`、`unknown_device`、`duplicate_sequence`、
`out_of_order_sequence`、`replayed_boot`、`uptime_regression`、
`database_constraint` 和 `database_error`。

结果和日志不得包含 MQTT 密码或完整 payload；需要排障时只记录 code、device_id、
boot_id 和 sequence。

# 阶段 0：状态、权限、图与 API v3 契约

> 状态：阶段 0 已冻结；实现必须保持向后兼容，不迁移真实数据库。
> 代码镜像：`backend/contracts.py`。文档与代码枚举变更必须同时提交。
> 阶段 1 数据基础：`docs/rebuild/device-state-foundation.md`。

## 1. 唯一事实来源

- Flask 后端的 v3 状态服务是设备状态、事件阶段和图快照的唯一事实来源。
- Web 和 APP 只展示后端结论，不在客户端重新判断在线、安全或事件角色。
- 旧 `/api` 接口在过渡期保留，但只供 admin/operator 使用，不再作为 APP 数据源。
- 真实模式请求失败时返回错误；客户端必须显示断线和最后成功同步时间，禁止回退到演示数据。

## 2. 稳定 ID

| 字段 | 格式与生命周期 |
|---|---|
| `device_id` | 2～64 位小写字母、数字、`_`、`-`；来自固件/登记，绑定 MAC 后保持稳定，IP 变化不变 |
| `incident_id` | 后端生成 UUID v4；一个事件从打开、恢复到解决始终不变 |
| `graph_id` | 后端生成 UUID v4；每个冻结的 60 秒图快照唯一且不可变 |
| `event_id` | SSE 单调递增的持久事件序号；用于 `Last-Event-ID` 补发或触发完整快照同步 |

数据库可保留内部整数主键，但所有跨端、跨表和外部 API 引用均使用上述稳定 ID。

## 3. 状态枚举

### 3.1 设备状态

| 维度 | 枚举 |
|---|---|
| `connection_status` | `unknown`、`online`、`stale`、`offline` |
| `security_status` | `unknown`、`safe`、`suspicious`、`under_attack`、`compromised`、`isolated` |
| `operation_mode` | `active`、`maintenance`、`disabled` |
| `detection_readiness` | `warming_up`、`ready`、`degraded` |
| `lifecycle_status` | `active`、`retired` |

默认心跳为 5 秒，15 秒进入 `stale`，30 秒进入 `offline`。后端接收时间用于
在线判定，设备自报时间只作为证据。`maintenance` 抑制预期离线告警但不删除
事件；`retired` 不进入当前图和在线统计。

### 3.2 事件状态与角色

| 维度 | 枚举 |
|---|---|
| `incident_stage` | `open`、`active`、`recovering`、`resolved`、`false_positive` |
| `incident_role` | `victim`、`compromised_source`、`external_source`、`unknown` |

快速规则负责 3～5 秒初判；GNN 只提供设备风险确认。事件角色必须结合流向、
稳定设备身份和数据新鲜度，GNN 高风险本身不能证明设备是受害者。

## 4. 权限矩阵

| 能力 | admin | operator | user |
|---|---:|---:|---:|
| Web 全社区设备/图/证据读取 | 是 | 是 | 否 |
| 添加、编辑、维护、退役、恢复、受控删除设备 | 是 | 否 | 否 |
| 修改 GNN、MQTT、抓包和系统配置 | 是 | 否 | 否 |
| 确认事件、标记误报、解决事件 | 是 | 是 | 否 |
| APP 查看授权设备和提醒 | 否 | 否 | 是 |
| APP “我已知晓”、联系管理员 | 否 | 否 | 是 |

- APP 只接受一次性配对码换取的短期、可吊销 user 令牌。
- user 令牌必须绑定设备 ID 或区域范围；任何列表和对象查询都在服务端过滤。
- “我已知晓”不改变安全事件阶段。
- 探针凭据、MQTT 设备凭据和浏览器/APP 凭据彼此独立，不能互换。

## 5. REST v3 契约

成功响应使用资源本身；失败统一为：

```json
{
  "error": {
    "code": "forbidden",
    "message": "权限不足",
    "request_id": "uuid"
  }
}
```

时间使用带时区的 ISO 8601 UTC 字符串。任何当前状态对象必须带
`state_version`、`observed_at`、`received_at` 和 `sources`。

阶段 1～5 按以下边界实现：

| 方法与路径 | 权限 | 语义 |
|---|---|---|
| `GET /api/v3/monitor` | admin/operator | 系统、全设备、当前 graph 摘要与活动事件完整快照 |
| `GET /api/v3/events` | admin/operator 或受限 user | SSE；user 只接收授权范围事件 |
| `GET /api/v3/devices` | admin/operator | 全设备列表与筛选 |
| `GET /api/v3/devices/{device_id}` | admin/operator，或范围内 user | 角色裁剪后的设备详情 |
| `GET /api/v3/devices/discovered` | admin | 待确认设备 |
| `POST /api/v3/devices` | admin | 添加或认领设备 |
| `PATCH /api/v3/devices/{device_id}` | admin | 修改允许的档案字段 |
| `POST /api/v3/devices/{device_id}/retire` | admin | 退役并吊销设备凭据 |
| `POST /api/v3/devices/{device_id}/restore` | admin | 恢复并重新预热 |
| `DELETE /api/v3/devices/{device_id}` | admin | 仅无历史引用的误添加设备，要求影响检查和名称确认 |
| `GET /api/v3/devices/{device_id}/traffic` | admin/operator，或范围内 user | user 响应删除 IP、端口和 GNN 特征 |
| `GET /api/v3/devices/{device_id}/peers` | admin/operator | 完整通信对象、方向和边权 |
| `GET /api/v3/graph/current` | admin/operator | 当前不可变图快照 |
| `GET /api/v3/graph/snapshots/{graph_id}` | admin/operator | 历史图快照 |
| `GET /api/v3/incidents` | admin/operator | 完整事件历史 |
| `POST /api/v3/incidents/{incident_id}/ack` | admin/operator | 管理员确认 |
| `POST /api/v3/incidents/{incident_id}/resolve` | admin/operator | 解决事件 |
| `POST /api/v3/incidents/{incident_id}/false-positive` | admin/operator | 标记误报并保留反馈 |
| `GET /api/v3/system` | admin/operator | 组件健康与降级原因 |
| `POST /api/v3/pairing/start` | admin | 创建一次性短期配对码 |
| `POST /api/v3/pairing/claim` | 匿名持码者 | 一次性换取受限 user 令牌 |
| `GET /api/v3/mobile/overview` | user | 仅授权设备和裁剪后的提醒 |
| `POST /api/v3/mobile/notices/{id}/read` | user | 标记本人已知晓 |
| `POST /api/v3/mobile/help-requests` | user | 联系管理员 |

## 6. SSE 事件信封

```text
id: 1842
event: device.connection_changed
data: {"event_id":1842,"occurred_at":"2026-09-19T08:00:00Z","state_version":17,"device_id":"door-01","payload":{"from":"stale","to":"offline"}}
```

允许的事件名固定为 `backend/contracts.py::REALTIME_EVENT_TYPES`。客户端重连时发送
`Last-Event-ID`；服务端无法补齐时发送 `snapshot.required` 控制信息并关闭流，客户端
重新拉取完整快照。APP 从后台回前台时不等待补发，直接先拉完整快照。

## 7. 图与模型契约

- 第一版窗口固定为 60 秒；训练、验证、推理必须使用同一特征顺序和图语义。
- 节点特征固定为 `backend/contracts.py::DEVICE_FEATURE_NAMES` 的 13 维顺序。
- `graph_id` 冻结后节点、边、原始特征、预测、模型版本均不可修改。
- 图边保存方向、协议、`flow_count`、`bytes`、`duration`，但当前 GAT 的
  `model_edge_capability` 固定为 `binary_adjacency`。
- UI 可以显示真实边特征，但必须明确写“当前模型未使用边特征”。
- 图质量不足、模型/归一化版本不匹配或特征域偏移时，检测就绪度为 `degraded`。

## 8. 设备与探针上报契约

MQTT 主题固定为 `community/{device_id}/status`。每台设备使用与 device_id 相同的
独立 MQTT 用户名，ACL 仅允许写自己的 status、读自己的 control。建议消息字段：

```json
{
  "schema_version": 1,
  "device_id": "door-01",
  "sequence": 42,
  "firmware_version": "0.2.0",
  "uptime_ms": 120000,
  "ip": "192.168.4.11",
  "mac": "AA:BB:CC:DD:EE:FF",
  "telemetry": {"state": "locked"}
}
```

探针 HTTP 请求使用独立 `X-Probe-Token`，不得使用 Web 会话或 MQTT 凭据。阶段 1
将强制校验探针 ID、序号、时间戳、请求大小、批大小与速率；当前阶段已先关闭匿名入口。

## 9. 设备删除语义

“退役”是默认操作。彻底删除必须同时满足：无事件引用、无图节点引用、无流量聚合
引用、无用户授权引用，并要求管理员输入设备名称二次确认。阶段 0 不执行任何数据库
迁移或删除；旧删除接口将在 v3 生命周期接口验收后隐藏。

# v3 monitor 快照与持久事件流

## 1. 当前范围

`backend/api/v3_realtime.py` 提供可注入数据库路径的独立 Blueprint：

- `GET /api/v3/monitor`：读取真实设备当前状态、系统组件健康和事件游标；
- `GET /api/v3/events`：从 SQLite 追加式事件日志补发并继续有界轮询；
- graph 和 incident 存储尚未实现，monitor 明确返回 `available: false` 及原因。

模块导入不会创建数据库、连接 MQTT 或启动线程。部署方必须先对显式数据库运行
v3 升级命令。正式 `backend/app.py::create_app()` 会用同一个显式数据库路径创建并
注册该 Blueprint；缺少路径或 schema 时路由仍然存在，但请求返回结构化 503，且不会
让 SQLite 创建文件。应用工厂和运行时生命周期见 `flask-application-factory.md`。

## 2. migration version 3

迁移 `realtime_event_log` 新增 `v3_realtime_events`：

| 字段 | 说明 |
|---|---|
| `event_id` | SQLite 自增整数主键，跨进程重启保持单调递增 |
| `event_type` | `contracts.py::REALTIME_EVENT_TYPES` 中的事件名 |
| `occurred_at` | 带时区 UTC ISO 8601 |
| `device_id` | 可空，设备事件引用稳定 ID |
| `state_version` | 可空，状态事件与对应当前状态版本一致 |
| `payload_json` | 事件特有的 JSON 对象 |

索引覆盖 `(event_type, event_id)` 和 `(device_id, event_id)`。迁移 checksum 为
`bfe9842f09d391284b408dd0df36e205e4ad9f92361ee304dd52955c9f6aa329`。version 1/2
定义和 checksum 均未修改。

设备观测、连接超时转换及组件健康变化在更新当前状态的同一 SQLite 事务中追加事件。
连接状态不变的合法遥测产生 `device.telemetry_updated`；重复、乱序、重放或校验拒绝的
MQTT 消息在进入状态事务前返回，因此不会产生事件。

设备增量事件的 payload 同时携带可直接投影到 monitor 设备行的
`connection_status`、`ip_address`、`observed_at`、`received_at` 和 `sources`；组件增量
携带 `readiness`、`started_at`、`ready_at`、`reason` 和 `updated_at`。这些字段与
`state_version` 所指向的状态在同一事务中写入，供 Web 在不重新拉取整份快照的情况下
安全更新。事件不包含 MAC、身份凭据或完整遥测正文。

设备档案或生命周期成功修改时追加 `device.inventory_changed`。其 payload 仅包含
`action`、稳定 `device_id` 和 `profile_version`；事件表中的 `device_id` 刻意为 NULL，
避免误添加设备被合法彻底删除后破坏追加式事件日志的外键。Web 收到该低频事件后进行
去抖且限频的 monitor 快照重取；五秒级遥测仍按设备增量更新，不触发整份快照。

未知候选使用 `device.discovered` 事件。payload 只含隔离候选 ID、候选状态和候选版本；
不含 MAC、IP 或完整观察证据。Web 可用该事件去抖刷新待确认列表，但事件不能把候选
投影成可信 monitor 设备。移动端不接收 discovery 事件。

## 3. monitor 用法

请求需要已登录的 `admin` 或 `operator`：

```http
GET /api/v3/monitor
```

响应含 `api_version`、`schema_version`、UTC `generated_at`、`event_cursor`、`devices`、
`system_components` 和 `capabilities`。每个设备包含档案字段、operation/connection 状态、
当前 IP、`state_version`、`observed_at`、`received_at` 和实际观测来源。响应设置
`Cache-Control: no-store`。空库返回空数组，不填充演示设备或默认健康状态。

读取快照前会用后端注入时钟刷新 `stale`/`offline`。刷新所产生的状态和事件先提交，
之后再读取一致的快照与 `event_cursor`。

## 4. SSE 建连与恢复

首次连接：

```text
GET /api/v3/monitor
GET /api/v3/events?after=<monitor.event_cursor>
```

断线重连发送标准 `Last-Event-ID`，且它优先于 `after`。游标必须是非负十进制整数。
持久事件按 `event_id` 严格递增补发，不为每个客户端创建线程或无界队列；每次查询后
关闭 SQLite 连接，再通过有界等待轮询新事件，所以慢客户端不会持有写锁或阻塞 MQTT。
空闲时只发送 `: keepalive` 注释，不分配 event_id，也不写数据库。

以下情况发送 `snapshot.required` 后关闭：

- 游标早于当前保留窗口或指向不存在的事件；
- 游标超前；
- 待补发事件超过单次上限；
- 事件 ID 有断档、类型未知或 payload 已损坏。

客户端收到该事件必须丢弃本地增量假设并重新读取 monitor。事件日志当前未实现自动
清理；将来增加保留策略时，必须保留“最小可补发游标”判定，不能静默跳过缺口。

## 5. 权限、错误与部署限制

当前仅 `admin`/`operator` 可访问两个端点。`user` 的设备授权范围尚未实现，因此明确
返回 403，不会临时泄露全局状态；匿名请求返回 401。流开始前的数据库或游标错误使用
统一 JSON 错误信封并携带 request_id；数据库路径不存在或 migration v3 未应用时返回
503，且不会创建文件。

当前推荐单 Flask 进程部署。事件事实和补发在 SQLite 中，因此进程重启和多进程读取
仍可恢复；但新事件通知使用每个请求自己的有界轮询，没有跨进程即时唤醒机制。若以后
改为多进程，延迟上限为轮询间隔，并应在引入外部通知总线后再缩短轮询，而不能把内存
广播当作事件事实来源。

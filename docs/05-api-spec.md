# 当前 API 目录

> 本文仅描述当前应用工厂实际注册的接口。旧版 Dashboard、独立 Alerts/Assets/Traffic/Analysis/Policy/Logs/Config、PCAP 上传、Excel 导出、blocklist 与 capture API 已移除；相关历史表及记录未删除。

基础地址为 `http://localhost:5000`，数据使用 UTF-8 JSON。所有 v3 写入均使用 Web Cookie session + CSRF 或受限 Mobile Bearer；探针使用单独的 probe credential。正常 API 请求不创建/升级数据库。

## 健康与登录

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| `GET` | `/api/health` | 公开只读 | 进程、数据库与 v3 schema 可用性；不暴露本地路径 |
| `POST` | `/api/auth/login` | 未登录 | 创建 Web session |
| `POST` | `/api/auth/logout` | Web session | 结束 Web session |
| `GET` | `/api/auth/me` | Web session | 当前用户与角色 |
| `GET` | `/api/v3/system/health` | admin/operator | 只读组件、完整性与容量状态；不连接网卡或运行维护 |

缺失或未升级的数据库会使 health 显示 `degraded`，数据库业务路由返回安全错误。升级/恢复步骤见 [数据库升级契约](rebuild/v3-database-upgrade.md) 与 [运维手册](12-operations-and-recovery.md)。

## 设备、监视和实时事件

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| `GET` | `/api/v3/monitor` | admin/operator | Web 实时快照与事件游标 |
| `GET` | `/api/v3/events?after=<event_id>` | admin/operator；受范围限制的 user | 持久 SSE 变化流 |
| `GET` | `/api/v3/devices` | admin/operator | 设备列表与筛选 |
| `POST` | `/api/v3/devices` | admin | 手动登记 |
| `GET` | `/api/v3/devices/{device_id}` | admin/operator | 设备详情 |
| `PATCH/DELETE` | `/api/v3/devices/{device_id}` | admin | 编辑或受控删除 |
| `POST` | `/api/v3/devices/{device_id}/retire` | admin | 退役并保留历史 |
| `POST` | `/api/v3/devices/{device_id}/restore` | admin | 恢复设备 |
| `GET` | `/api/v3/devices/discovered`、`/api/v3/devices/discovered/{candidate_id}` | admin | 隔离候选设备 |
| `POST` | `/api/v3/devices/discovered/{candidate_id}/claim|ignore|restore` | admin | 认领、忽略或恢复候选 |
| `GET` | `/api/v3/devices/{device_id}/traffic` | admin/operator；范围内 user | 设备分钟流量、速率和协议详情 |
| `GET` | `/api/v3/devices/{device_id}/peers` | admin/operator | 设备通信对象与方向 |

设备流量仅从设备详情使用。时间为带时区的 ISO 8601，查询上限、隐私裁剪、聚合字段和 `no_samples` 语义见 [设备流量契约](rebuild/v3-device-traffic.md)。实时页面先获取 monitor 快照，再用返回游标建立 SSE。

## 安全事件和求助

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| `GET/POST` | `/api/v3/incidents` | admin/operator | 查询历史或受控创建事件 |
| `GET` | `/api/v3/incidents/{incident_id}` | admin/operator | 事件证据与时间线 |
| `POST` | `/api/v3/incidents/{incident_id}/ack|recovering|resolve|false-positive` | admin/operator | 记录事件操作和处置时间线 |
| `GET` | `/api/v3/help-requests`、`/api/v3/help-requests/{help_request_id}` | 管理员端；本人范围 user | 求助列表和详情 |
| `PATCH` | `/api/v3/help-requests/{help_request_id}` | admin/operator | 管理员更新求助状态 |
| `GET/PUT` | `/api/v3/support-contact` | admin 读写；按接口限制 | 公开支持联系人 |

探针 v2 batch 可附带规则事件。仅在 batch 首次被接受、事件目标 IP 唯一绑定到已登记设备时，事件才进入 v3 workflow 和范围裁剪后的移动通知。旧无版本 payload 明确返回 `not_ingested`，不会继续走已删除的旧 alerts 表处理链。

## 移动用户与 APP

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| `GET/POST` | `/api/v3/mobile-users` | admin | 列表或创建移动用户 |
| `GET/PATCH` | `/api/v3/mobile-users/{user_id}` | admin | 移动用户资料 |
| `GET/PUT` | `/api/v3/mobile-users/{user_id}/scopes` | admin | 用户设备/区域范围 |
| `GET` | `/api/v3/mobile-sessions` | admin | 活跃会话 |
| `POST` | `/api/v3/mobile-sessions/{session_id}/revoke` | admin | 撤销会话 |
| `POST` | `/api/v3/pairing/start` | admin | 创建一次性配对码 |
| `POST` | `/api/v3/pairing/claim` | 持有效配对码者 | 领取受限 APP 会话 |
| `GET` | `/api/v3/mobile/session`、`/api/v3/mobile/overview` | 已配对 user | 当前会话与本人设备/提醒 |
| `GET` | `/api/v3/mobile/devices/{device_id}`、`/api/v3/mobile/devices/{device_id}/traffic` | 范围内 user | 本人设备详情及裁剪流量 |
| `GET` | `/api/v3/mobile/notices[/{incident_id}]` | 已配对 user | 本人事件提醒 |
| `POST` | `/api/v3/mobile/notices/{incident_id}/read|acknowledge` | 本人事件范围 user | 阅读或知晓提醒 |
| `GET/POST` | `/api/v3/mobile/help-requests[/{help_request_id}]` | 本人范围 user | 创建、查询本人求助 |
| `GET` | `/api/v3/mobile/support-contact` | 已配对 user | 获取支持联系人 |
| `POST` | `/api/v3/mobile/token/refresh`、`/api/v3/mobile/logout` | 已配对 user | 刷新或注销令牌 |

移动令牌不能替代浏览器 session、探针凭据或 MQTT 设备身份。服务端对列表、详情、流量和提醒逐项应用设备/区域范围。

## Probe 与设备状态上报

以下兼容接口仍有边缘客户端实际调用，不能因旧页面移除而删除：

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| `POST` | `/api/probe/register`、`/api/probe/heartbeat` | probe credential | 探针登记与心跳兼容状态 |
| `POST` | `/api/probe/push` | probe credential | schema v2 batch 幂等汇入设备流量；可携带规则事件 |
| `POST` | `/api/probe/status-report` | probe credential | 探针启停状态 |
| `GET` | `/api/probe/control-status` | probe credential 或 admin | 探针控制轮询 |
| `POST` | `/api/probe/control` | admin | 记录探针控制指令 |
| MQTT | `community/{device_id}/status` | 每设备独立凭据 | MQTT v2 状态/发现上报与控制主题订阅 |

不再注册旧 probe list/status 只读路由；系统健康页和 v3 设备/发现视图承担新版观察入口。MQTT、probe、REST 登录和移动端令牌相互隔离。详见 [MQTT subscriber 契约](rebuild/mqtt-subscriber.md)、[心跳 v2](rebuild/mqtt-heartbeat-v2.md) 和 [状态/API 契约](rebuild/state-and-api-contract.md)。

## 已退役旧入口

下列路径不再注册，客户端会收到 404：`/api/dashboard/*`、旧 `/api/alerts`、`/api/assets`、旧独立 `/api/traffic/*`、`/api/analysis/*`、`/api/policy/*`、`/api/logs/*`、旧 `/api/config`、`/api/detect/upload`、`/api/export/excel`、`/api/capture/*` 和旧 alert block/unblock。SQLite 内旧表和既有行保留，未执行数据删除或结构迁移。

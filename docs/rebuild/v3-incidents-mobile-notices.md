# v3 安全事件、移动提醒与帮助请求

本里程碑建立记录型安全事件工作流。事件来源只允许 `manual`、`rule`
和 `system`。它不启用 GNN，也不把“没有已记录事件”解释为系统安全或
没有攻击。

## Migration v8

迁移名为 `incident_and_mobile_notice_workflow`，只能通过现有显式升级
命令对指定数据库副本执行。它不会导入旧 `alerts`，也不会创建演示事件
或默认联系人。

新增对象：

- `v3_incidents`：事件主记录及乐观并发版本。
- `v3_incident_devices`：`affected`、`suspected_source`、
  `observer` 和 `unknown` 设备角色。
- `v3_incident_timeline`：公开进度与内部详情分列的处置时间线。
- `v3_mobile_notice_acknowledgements`：逐用户的阅读和“我已知晓”。
- `v3_mobile_notice_changes`：稳定的移动增量游标事实来源。
- `v3_help_requests`、`v3_help_request_timeline`：求助及处理进度。
- `v3_support_contacts`：可开关、带版本的公开联系方式。
- `v3_incident_workflow_audit`：不保存正文和秘密的管理审计。

事件设备及帮助请求中的设备引用会阻止设备被彻底删除。migration v1～v7
的内容和 checksum 不变。

## 事件状态机

```text
open -> acknowledged -> recovering -> resolved
  |          |              |
  +----------+--------------+-> false_positive
```

- `open`：新建的已记录事件。
- `acknowledged`：管理员/operator 已开始处理，与用户阅读无关。
- `recovering`：处置后正在恢复。
- `resolved`、`false_positive`：终态，本版本不支持 reopen。

每次转换必须提交 `expected_incident_version`。事件、timeline、审计、
移动变更记录和 SSE 在同一 SQLite 事务内提交。

管理 API 只允许 admin 创建 `manual` 事件。`rule` 和 `system` 事件应由
可信后端服务使用 system actor 写入。`gnn` 或混合 GNN 来源会被拒绝。

## 管理 API

Web Cookie session 使用：

- `GET /api/v3/incidents`
- `GET /api/v3/incidents/{incident_id}`
- `POST /api/v3/incidents`
- `POST /api/v3/incidents/{incident_id}/ack`
- `POST /api/v3/incidents/{incident_id}/recovering`
- `POST /api/v3/incidents/{incident_id}/resolve`
- `POST /api/v3/incidents/{incident_id}/false-positive`

admin/operator 可以读取和处置，只有 admin 可以创建 manual 事件。所有
写请求发送 JSON 和从成功 GET 响应头取得的 `X-CSRF-Token`。user Web
session 返回 403，Mobile Bearer 不能代替 Web session。

用户文案和 `public_progress` 会拒绝 IP、MAC、端口、graph ID、GNN、
模型版本和原始规则表达式等技术细节。

## 移动提醒字段边界

Mobile Bearer 使用：

- `GET /api/v3/mobile/notices`
- `GET /api/v3/mobile/notices/{incident_id}`
- `POST /api/v3/mobile/notices/{incident_id}/read`
- `POST /api/v3/mobile/notices/{incident_id}/acknowledge`

用户只有在当前 device/area scope 覆盖至少一个
`user_visible=true` 的 `affected` 设备时才能看到提醒。area 归属及
scope 每次请求都重新计算；撤销 scope 后下一次请求立即不可见。
`suspected_source`、`observer` 和隐藏设备不会扩大权限。

移动响应只包含用户标题、用户摘要、简化级别、可见受影响设备、时间、
状态、公开进度和当前用户自己的阅读/知晓状态。它不包含管理员标题、
内部详情、suspected source、IP、MAC、端口、graph ID、GNN/模型字段、
原始规则或其他用户的确认状态。

`read` 记录首次阅读；`acknowledge` 表示“我已知晓”。两者均幂等，
不会改变全局 incident status，也不会制造管理员已处置的状态。

## 增量游标

首次不传 `after`，返回完整快照和 `next_cursor`。游标格式为
`<change_id>:<scope_version>`，不携带事件正文。

后续调用 `GET /api/v3/mobile/notices?after=<cursor>&view=active&limit=50`。
服务端仍重新校验当前 scope。scope version 变化、待补变化超过上限或
游标不可连续使用时返回 `snapshot_required=true`，客户端应重新拉完整
快照。已解决或 false-positive 对活动列表产生 tombstone。猜测游标不能
访问未授权事件。

## 公开联系人

- `GET /api/v3/support-contact`：admin/operator。
- `PUT /api/v3/support-contact`：admin + CSRF。
- `GET /api/v3/mobile/support-contact`：Mobile Bearer。

未配置或禁用时返回 `available=false`，不生成默认电话或邮箱。更新必须
提交 `expected_config_version`。移动端只得到启用联系人的公开字段，
不返回更新管理员账号。

## 帮助请求

移动端使用：

- `POST /api/v3/mobile/help-requests`
- `GET /api/v3/mobile/help-requests`
- `GET /api/v3/mobile/help-requests/{help_request_id}`

创建必须带 `Idempotency-Key`。同一用户、session 和 key 的相同正文
返回原请求；不同正文返回 409。可选 incident/device 必须在当前 scope，
用户只能查询自己提交的请求。

管理端使用：

- `GET /api/v3/help-requests`
- `GET /api/v3/help-requests/{help_request_id}`
- `PATCH /api/v3/help-requests/{help_request_id}`

状态为 `open`、`in_progress`、`waiting_for_user`、`closed`。更新必须带
`expected_request_version`。`public_response` 立即对用户可见，
`internal_note` 永不进入移动响应。

## Monitor 与能力语义

应用 migration v8 后，`/api/v3/monitor` 返回真实活动/最近事件摘要和：

- `capabilities.incident.available=true`；
- `empty_meaning=no_recorded_incidents_not_proven_safe`。

`capabilities.graph.available` 继续为 false。Mobile overview 同样明确
`gnn.available=false`，并提供未读、未确认数量和最近提醒。数量为零只
表示没有已记录且当前可见的提醒，不是安全结论。

当前移动增量接口为轮询模型。后续 Web 事件管理页可直接使用管理 API 和
现有 SSE；APP 提醒页使用 Mobile Bearer、快照/游标及 read/ack 接口。

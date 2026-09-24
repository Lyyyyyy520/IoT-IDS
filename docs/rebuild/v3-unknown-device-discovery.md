# v3 未知设备隔离发现与人工认领

本工作流把可疑的未知身份保存在独立候选区。观察到的身份不是设备事实：候选不会成为可信设备、在线状态、用户授权范围、事件受影响设备或可信流量归属，也不会进入 `/api/v3/monitor` 和移动 overview。

## Migration v9

`unknown_device_discovery` 只由 `backend/v3_db_upgrade.py plan/apply` 显式执行，checksum 为
`cbd47c1e4e2807b770b7de3e5e532ef40af34d931bf417565f62254e57e363f2`。v1～v8 的语句和 checksum 保持不变；不导入 legacy assets，不创建候选样例。

- `v3_discovered_device_candidates` 保存 MAC 身份候选、隔离状态、IP 线索、提议 ID、计数和乐观版本。
- `v3_discovery_observations` 追加来源和脱敏证据；唯一键防止同一来源事件重试膨胀。
- `v3_discovery_actions` 追加管理员认领、忽略、恢复动作及 actor、request ID 和前后状态。

不存完整 MQTT payload、原始报文、密码、token 或 Cookie。已有候选与观察没有普通删除接口。候选/观察总量和每候选数量有上限；同一 MAC、同一来源每分钟也有限频额。容量耗尽时服务组件记为 `degraded/candidate_capacity_reached`，可信设备心跳的状态写入不因此被候选容量逻辑修改。过期数据清理留待单独维护任务，不由 GET 请求执行。

## 身份、来源与冲突

来源枚举为 `mqtt_unknown`、`dhcp`、`arp`、`probe`、`other`。第一版必须提供合法 MAC，规范化后按 MAC 唯一合并。单独 IP 不足以创建候选；普通流量 IP 不接入发现服务。DHCP/ARP/probe 当前仅有可注入服务入口，没有连接现场网络或采集器。

未知 MQTT 心跳须先通过现有完整版本化信封、主题、device ID、MAC/IP、大小及字段校验。topic/payload 不一致、非法数据或超大消息仍按原校验拒绝，不进入候选。合法但 device ID 未登记的心跳只写入候选证据，并返回 `unknown_device_discovered`；不写 trusted state、心跳观测、boot cursor 或 IP 绑定。稳定 dedup key 由 boot 会话和 sequence 组成，MAC 不同则仍视为不同身份候选。

同一 MAC 声称多个 device ID 时标记 `conflict/multiple_proposed_device_ids`。相同来源的 boot/sequence 去重键若重复出现不同的稳定身份/IP证据，则追加一条去重关联的脱敏冲突观察并标记 `conflict/deduplication_key_reused`；同一修改证据重试仍会去重。已登记 MAC 再出现时标记 `conflict/mac_already_bound`，该候选不可认领；须先核对已有可信档案。已认领候选仍保持 `claimed` 终态，但后续不同 device ID 声称会留下冲突原因和事件，不会修改可信设备档案。MAC/IP 都只是未认证观察证据，不能代替 Broker ACL 或真实设备身份验证。

## 候选状态和管理接口

允许 `pending → ignored`、`ignored → pending` 和 `pending/conflict → claimed`；`claimed` 为终态。被忽略候选的后续观察只更新证据，不自动重新打开。每次人工修改均要求 `expected_candidate_version`。

接口仅 Web Cookie session 可用：

- `GET /api/v3/devices/discovered`、`GET /api/v3/devices/discovered/{candidate_id}`：admin/operator 只读；operator 响应省略 MAC、IP 和证据哈希。
- `POST .../{candidate_id}/claim`、`/ignore`、`/restore`：仅 admin，并要求与设备管理相同的 `X-CSRF-Token`。

认领时管理员需手填稳定 `device_id` 和完整设备档案；`proposed_device_id` 仅供人工参考。多个提议 ID 冲突必须勾选并提交 `resolve_identity_conflict=true`，明确由手填身份解决；已绑定 MAC 冲突不可通过该方式覆盖。认领、候选版本变更、可信设备创建、初始 `unknown` 状态、设备管理审计及 `device.inventory_changed` 在同一事务完成。候选 IP/历史观察不会复制成在线状态或可信流量历史。

认领响应的 `credential_provisioning_required: true` 是强制提示。系统不生成 Mosquitto 密码、不改 ACL，也不自动赋予任何移动用户设备 scope。现场完成人员核验、专属设备账号和仅允许发布自身 `community/{device_id}/status` 的 ACL 后，必须等待新的合法认证心跳才会变为 online。

忽略必须提供原因；恢复仅清除忽略状态并回到 pending，不创建可信设备。已认领 candidate 及设备删除引用会永久阻止该设备的“误添加物理删除”；应退役保留证据。

## 实时事件与 Web

首次候选和受限状态变化通过 `device.discovered` 通知，payload 仅含 `candidate_id`、候选状态和候选版本，不含 MAC、IP、payload 或遥测。claim 另产生标准 `device.inventory_changed`。Web `/devices` 的“待确认设备”页复用 monitor SSE store；发现事件只去抖刷新隔离候选列表，不将候选加入 Monitor trusted snapshot。Mobile 不订阅 discovery 事件，也没有 discovery API。

### 现场接入前必须完成

1. 在离线数据库副本上执行 v9 `plan`/`apply` 并核对备份、checksum 和表对象。
2. 用隔离 Mosquitto 测试仅订阅 `community/+/status` 的后台账号与设备专属 ACL；确认未知设备心跳不能写 trusted state。
3. 定义真实 DHCP/ARP/probe 适配器的来源标识、观察 ID、去重和频率预算，不读取完整包或凭据。
4. 由管理员核验候选的物理标签/MAC 和部署位置，显式填写 device ID；人工配发独立 Broker 账号与 ACL。
5. 确认启动预热、容量退化、并发 admin 冲突和数据库恢复演练，以及多进程中仅一个 MQTT subscriber 的部署约束。

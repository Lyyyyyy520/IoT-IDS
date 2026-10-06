# v3 设备流量聚合与只读 API

当前 v3 流量事实按稳定 `device_id` 查询；旧 `traffic_logs` 不自动迁移。现场 probe 经 v2 batch 接入，历史分钟聚合以 SQLite 为事实来源；进程内实时窗口只用于短期速率展示。

## 现有来源审计

| 来源 | 原始含义 | 时间/大小 | 幂等能力 | v3 处理 |
| --- | --- | --- | --- | --- |
| `traffic_logs` | legacy 单包记录 | `timestamp` 为本地 SQLite 时间；`length` 为抓包长度 | 无 sample/batch ID | 不自动迁移 |
| VM probe | 每个 `flow` 实际是一个包 | `len(pkt)`；Scapy 包时间转 UTC | v2 会话、batch sequence、sample ID | 通过统一聚合服务 |
| Raspberry Pi probe | tcpdump 文本中的一个包 | 使用 `-tt` epoch 和 `length N`；不再硬编码 100 字节 | v2 会话、batch sequence、sample ID | 通过统一聚合服务 |

旧无版本 probe 请求不再进入旧检测链；响应明确返回 `legacy_probe_schema_no_idempotency`，不会写 v3。只有成功接收的 v2 batch 会被处理，重复 batch 不重复汇总或创建事件。

## Migration v5

`device_traffic_aggregation` 的 checksum 是
`77e7011d94ef2b3a7022013fbef0c2e68f70f1ca9ead58446dd3d4bde78c74c6`。
它只新增以下对象，v1～v4 不变：

- `v3_device_ip_bindings`：设备 IP 的半开有效区间 `[valid_from, valid_to)`；
- `v3_device_traffic_minutes`：每设备每分钟 TX/RX 字节、包、流；
- `v3_device_traffic_protocol_minutes`：方向和网络层/传输层协议分布；
- `v3_device_traffic_peer_minutes`：通信对象、方向、协议聚合；
- `v3_traffic_ingest_batches`：source/session 下的 batch ID 与 sequence 去重；
- `v3_traffic_ingest_samples`：sample ID 去重账本，只含标识和时间；
- `v3_traffic_unassigned_minutes`：未映射或映射冲突样本的汇总质量指标。

所有表只保存聚合元数据，不保存报文载荷、Cookie、账号或凭据。升级仍只能通过：

```powershell
python backend/v3_db_upgrade.py plan --database <副本.sqlite>
python backend/v3_db_upgrade.py apply --database <副本.sqlite> --backup-dir <备份目录>
```

## TrafficSample 与归属

统一样本包含 `source_id`、`sample_id`、`occurred_at`、`received_at`、源/目标
IP、确定的 `network_protocol`、可选应用协议及 `application_protocol_inferred`、
端口、字节、包和流计数。时间必须带时区并转为 UTC；数值、端口、IP、协议、
未来偏差、30 天保留窗口和批次大小都在写入前校验。

协议聚合只使用样本明确提供的 `network_protocol`。即使未来增加端口推断，也必须
设置 `application_protocol_inferred=true`，不能把推断结果冒充确定事实。

可信设备状态观测使用后端 `received_at` 更新 IP 历史。IP 改变时关闭旧区间并开启
新区间；流量按自身 `occurred_at` 查询当时有效绑定。同一时间同一 IP 匹配多个设备
时返回 `ambiguous_ip_binding` 并进入未归属汇总，不猜设备；未知 IP 也不会创建设备。
流量写入永远不修改 MAC 身份。

源设备计 TX、目标设备计 RX。两端均受管时双方各写一条且互为 peer；外部对象的
`peer_device_id` 为 `null`。批次登记、sample 去重、分钟/协议/peer 聚合在一个事务
提交，失败整体回滚。维护、disabled 和退役设备仍保留真实流量，但本层不产生安全
结论。

## Probe v2 信封

```json
{
  "schema_version": 2,
  "source_id": "probe:7",
  "source_session_id": "5c7a...",
  "batch_id": "5c7a...-18",
  "batch_sequence": 18,
  "probe_id": 7,
  "probe_name": "Pi-001",
  "alerts": [],
  "flows": [{
    "sample_id": "5c7a...-302",
    "occurred_at": "2026-09-21T08:29:45Z",
    "src_ip": "192.168.1.10",
    "dst_ip": "203.0.113.20",
    "network_protocol": "TCP",
    "src_port": 51000,
    "dst_port": 443,
    "bytes": 128,
    "packets": 1,
    "flow_count": 0
  }]
}
```

VM/Pi probe 和保留的 edge detector 均通过 v2 信封；来源为包观察时 `packets=1`、`flow_count=0`。edge detector 的本地研究模型目前使用占位流特征，其分数只在终端显示，不会创建 v3 事件。服务端只选择 TrafficSample 白名单字段，legacy payload 不会进入 v3 表。

## 查询 API

admin/operator 可访问；user 在设备授权范围实现前返回 403，匿名返回 401。所有响应
带 `Cache-Control: no-store` 和 `X-Request-ID`。

```text
GET /api/v3/devices/camera-01/traffic?from=2026-09-21T08:00:00Z&to=2026-09-21T10:00:00Z&resolution=5minute&protocol=TCP
GET /api/v3/devices/camera-01/peers?from=2026-09-21T08:00:00Z&to=2026-09-21T10:00:00Z&direction=tx&sort=bytes&limit=50&offset=0
```

`traffic` 返回实际窗口、分辨率、availability/freshness、可用时的短期速率、汇总、
时间序列、协议分布和未归属数据提示。没有样本时 `available=false`、
`reason=no_samples` 且 `summary=null`，不会用全零表示已确认无流量。

`peers` 支持方向/协议筛选、bytes/packets 排序及稳定分页。管理员可见 peer IP；
operator 的 `peer_ip=null` 且 `peer_ip_visible=false`。已退役设备仍可查历史。

单次范围最多 30 天，分辨率为 `auto/minute/5minute/hour`，并限制最大数据点。
数据库不存在或 v5 未应用时返回统一 503，且不会创建文件。

## 实时窗口与保留

默认实时窗口保存最近 120 秒、按秒聚合，并限制最大设备数和每设备 bucket 数；不含
payload。后端重启后窗口为空会返回 `warming_up` 与明确原因，不用零速率假装可用。
该缓存是单进程内状态：多进程 WSGI 下各 worker 不共享实时速率，应由单独采集 worker
或共享实时存储承载；SQLite 历史补查仍正确。

30 天清理只能由显式维护任务调用 `DeviceTrafficService.purge_before(...)`，普通 GET
不会执行大规模删除。清理分钟聚合与去重账本，不修改设备档案或 IP 绑定历史。

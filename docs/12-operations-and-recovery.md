# 运行维护与故障恢复

本手册覆盖 IoT-IDS SQLite 数据保留、维护、健康检查和恢复。在线运行服务不得自动建库、迁移或清理；自动维护默认关闭。所有生产维护由值班负责人提出、数据库责任人复核，并在停写窗口执行。

## 数据保留默认值

期限按 UTC 时间计算，均可用 `IOT_IDS_RETENTION_<POLICY>_DAYS` 配置。允许范围为 1–36500 天；无效整数或越界值会拒绝计划和执行。默认每张表每次最多处理 1000 行，CLI 上限为 100000 行；重复执行可逐批完成。

| 数据策略 | 默认期限 | 清理条件与保护 |
|---|---:|---|
| 原始流量日志 | 30 天 | 过期记录 |
| 实时事件日志 | 30 天 | 仅按最老连续前缀清理；活动事件证据会阻断前缀 |
| 设备观测 | 90 天 | 仍被当前状态或 IP 绑定引用的观测保留 |
| MQTT 启动历史 | 180 天 | 当前 MQTT cursor 指向的启动记录保留；cursor 本身永不清理 |
| 设备 IP 绑定历史 | 365 天 | 当前开放绑定保留 |
| 流量分钟聚合 | 365 天 | 仅过期聚合记录 |
| 流量去重批次和样本 | 90 天 | 每个 source/session 的最新序号及当前去重状态保留 |
| 未知设备观测 | 180 天 | pending 候选及其观测保留；已认领或忽略候选的过期观测可清理 |
| 移动配对记录 | 30 天 | 未过期、刚认领或刚失效的配对保护 |
| 移动 session | 90 天 | 未过期 session、关联求助和 refresh history 的 session 保留 |
| refresh history | 90 天 | 当前 refresh generation、有效 session 或有关联求助的历史保留 |
| 移动限流桶 | 2 天 | 当前仍封禁的桶保留 |
| 事件处置时间线 | 2555 天 | 未结束 incident 的时间线和证据保留 |
| 求助时间线与已关闭求助 | 2555 天 | 未完成求助及仍引用时间线的记录保留 |
| 用户确认记录（notice acknowledgements） | 2555 天 | 活跃 incident 的确认记录保留；已结束 incident 的旧确认可清理 |
| 移动通知变更游标 | 2555 天 | 独立于确认记录；只裁剪最旧连续前缀，活跃 incident 会阻断前缀 |
| 管理与安全审计 | 2555 天 | 与 incident evidence、用户确认分开配置；不清理仍被业务记录引用的数据 |

当前设备状态、pending 未知设备候选、当前 MQTT cursor、有效移动 session、refresh generation、未完成求助、活动 incident、仍被引用的数据都不由本策略删除。此 CLI 不改流量统计口径或事件状态机，也不执行 VACUUM。

## 计划与显式执行

`plan` 只接受已存在的 v9 数据库，并严格拒绝 WAL、SHM、journal sidecar；它使用 immutable 只读连接并检查源文件指纹。生产现场仍应先停止写入进程和设备采集器。`apply` 需要已存在的备份目录，会先取得 SQLite 写事务锁；存在 journal 或不完整的 WAL/SHM 组合时拒绝执行。配对的 WAL/SHM 由 SQLite backup API 一致性备份处理，活跃写入者会以 `database_busy` 或 `database_locked` 失败。不要手动删除 sidecar 或直接复制活跃 WAL 文件。

```powershell
python backend/v3_db_maintenance.py plan `
  --database D:/service-data/iot-ids.sqlite `
  --now 2026-09-24T20:00:00+08:00 --json

python backend/v3_db_maintenance.py apply `
  --database D:/service-data/iot-ids.sqlite `
  --backup-directory D:/service-data/backups `
  --now 2026-09-24T20:00:00+08:00 --batch-limit 1000 --json
```

`plan` 不修改数据库、不创建 sidecar 或数据库，不输出 MAC、IP、正文、token/hash、备份路径或绝对数据库路径。JSON 按固定 key 排序，逐表列出默认/最小/最大期限、截止时间、预计和本批数量、可清理时间范围及稳定原因码。`apply` 会先校验源库 schema/checksum 和 integrity，再用 SQLite backup API 写入临时备份、校验后原子命名；备份名为 `iot-ids-maintenance-<UTC 时间>[-序号].sqlite`。随后在 `BEGIN IMMEDIATE` 事务内分批清理，并在提交前对事务中的修改后数据库执行 `integrity_check`。任一数据库操作失败都回滚事务；完整性校验通过的备份保留用于恢复。相同 `--now` 重复执行是幂等的，每批继续推进。成功 JSON 只返回备份已验证标记、计数、UTC 时间与稳定原因码，不返回备份路径；操作人应按 UTC 运行时间在已审核目录确认生成的备份文件。

示例 JSON 结构：

```json
{"automatic_maintenance":false,"batch_limit_per_table":1000,"integrity_ok":true,"observed_at":"2026-09-24T12:00:00Z","operation":"plan","policies":[{"cutoff_at":"2026-08-25T12:00:00Z","default_retention_days":30,"eligible_rows":0,"maximum_retention_days":36500,"minimum_retention_days":1,"newest_eligible_at":null,"oldest_eligible_at":null,"planned_rows":0,"policy":"realtime_events","protection_reason_code":"active_incident_event_or_first_nonexpired_event_stops_prefix","protected_rows":0,"retention_days":30,"selection_reason_code":"expired_oldest_event_prefix","table":"v3_realtime_events"}],"schema_version":9}
```

执行前由运维负责人审阅 JSON 并记录工单；备份管理员确认备份位于受控目录且 integrity 通过。不要把备份覆盖到现有文件。维护命令不调用 VACUUM，也没有后台计划任务。

## 健康 API 与 Web 页面

`GET /api/v3/system/health` 只读检查数据库存在性、可读写标志、migration/checksum、`integrity_check`、组件状态、事件 cursor 和容量；不会建库、迁移、备份、清理、VACUUM 或连接 MQTT/网卡。admin/operator 可通过 Web session 读取；普通 user 为 403，Mobile Bearer 为 403，匿名为 401。响应不含数据库路径、环境变量、凭据、MAC/IP、异常堆栈或正文。

Web 的懒加载页面为 `/system-health`，仅 admin/operator 可访问。页面明确区分 `ready`、`warming_up`、`degraded`、`unavailable`；未知值按不可用展示。页面不提供清理、备份、迁移或 compact 操作。`last_apply_at` 从事务内健康记录读取；`plan` 必须严格只读，所以 `last_plan_at` 不持久化并带 `maintenance_plan_read_only` reason code。

## 备份恢复

恢复前停止 Web/API、MQTT subscriber、probe/collector 和所有其他写入进程。把故障库及其全部 sidecar 原样复制到只读故障留存位置；不得只删 WAL、SHM 或 journal。确认目标路径和备份身份后，在隔离恢复环境使用 SQLite Backup API 把已验证备份恢复到一个新文件，再执行 `PRAGMA integrity_check` 和应用 schema/checksum 检查；验证通过后由数据库责任人与值班负责人共同批准切换配置。保留故障库和工单记录，不覆盖唯一副本。

恢复演练可在临时副本使用以下最小流程（禁止用于正在服务或尚未确认的数据库）：

```python
import sqlite3

with sqlite3.connect("verified-backup.sqlite") as source:
    with sqlite3.connect("restored-copy.sqlite") as target:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError("restored copy failed integrity_check")
```

只有完整性、schema checksum、应用读写和备份可回读都通过后才可切换。由当班数据库责任人负责检查，值班负责人批准恢复；缺少任一责任人时停止操作并升级处理。

## 故障处理与运行限制

- `database_file_missing`：核对配置和恢复介质；服务不会自动建立空库。
- `schema_checksum_mismatch` / `schema_incomplete`：停止写入，核对发布版本与备份；维护不会迁移 schema。
- `database_read_only`：核对文件及父目录权限。不要尝试用新空库替代。
- `database_busy` / `database_locked`：列出本机写入进程和维护任务，等待事务结束；不要删除锁文件或 sidecar。
- `database_disk_full` / `database_io_error`：先恢复磁盘容量和文件系统健康，再从可验证备份恢复评估。
- `database_wal_mode_unsupported`、`database_sidecar_present`、`database_journal_present` 或 WAL/SHM 不匹配：停止所有进程，保留整组文件，通过 SQLite API 做隔离副本和恢复检查；不要自行删 sidecar。
- 维护前/中完整性检查失败：不继续清理；事务内失败会回滚。保留失败库、journal 和日志用于责任人复核。
- 自动维护固定关闭。MQTT subscriber 不会由多进程 WSGI worker 启动；需要 MQTT 时使用一个单实例后端进程。debug reloader 父进程不启动服务。SQLite 锁冲突应等待写事务结束，不能删除锁文件或 sidecar。
- runtime start/stop 对同一 app 实例可重复调用。启动部分失败会停止已创建的 subscriber；修复数据库/配置后可再次启动。停止时请求停止该 app 拥有的 capture service 并等待采集线程退出；超时记录稳定 `worker_stop_timeout` reason code。

## SSE 重同步与现场验收

事件与移动通知变更清理只删除过期连续前缀，不会重编号 ID。落后于保留窗口、遇到 ID 断档或游标无法证明连续时，SSE/notice API 返回 `snapshot.required`，客户端必须重新 GET 当前快照并从快照给出的 cursor 开始订阅；不得补发或合成未知事件。

发布或维护后由现场责任人检查：健康 API 中数据库/schema/integrity/API 状态与 reason code；`plan --json` 的时间范围和受保护数量；备份恢复演练结果；SSE 断档客户端能否重同步；进程数是否为单 worker；故障注入下 busy/read-only/disk-full/WAL reason 是否稳定。生产磁盘满、异常进程退出和真实文件权限应在受控副本现场演练，本地测试不连接部署数据库或设备。

若 plan 过量、发现活动/引用记录将被清理、备份完整性失败、源库在计划期间变化、SQLite 返回异常或责任人无法确认恢复点，停止 apply。若提交后验收失败，停写、保留故障库及 sidecar，并按双人批准的恢复流程切换到 verified backup；不要现场 VACUUM 或手工改 schema。

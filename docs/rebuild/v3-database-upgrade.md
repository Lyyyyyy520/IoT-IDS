# v3 数据库升级命令与现场演练

## 1. 命令边界

入口为 `backend/v3_db_upgrade.py`。它不导入 Flask 应用或旧的
`backend/database.py`，因此不会隐式选择 `backend/data/ids.db`，也不会触发旧表
初始化和演示数据写入。

所有操作必须显式传入已有数据库路径。路径不存在、不是普通文件或不是 SQLite 3
文件时，命令会报错退出，绝不会让 SQLite 静默创建新库。

退出码：

| 退出码 | 含义 |
|---:|---|
| `0` | 操作成功 |
| `2` | 命令行参数错误 |
| `3` | 数据库或备份目录路径错误 |
| `4` | 非 SQLite、无法检查或完整性失败 |
| `5` | 备份创建或验证失败 |
| `6` | 迁移冲突或事务回滚 |

## 2. 在真实库副本上演练

以下示例路径只是占位符，必须替换为现场确认后的绝对路径。首次演练不得把
`backend/data/ids.db` 作为 `plan` 或 `apply` 的参数。

### 2.1 使用 SQLite backup API 制作一致性副本

先创建一个独立、空间充足且不受应用进程写入的备份目录，然后执行：

```powershell
python backend/v3_db_upgrade.py backup `
  --database "D:\IoT-IDS-live\ids.db" `
  --backup-directory "D:\IoT-IDS-audit-copies"
```

该命令通过 SQLite backup API 读取主库和未检查点的 WAL 内容，不使用普通文件
复制。输出中必须同时出现源库和备份的 `integrity_check: ok`。生成的 `.sqlite`
文件就是后续演练副本，原库 schema 不会改变。

### 2.2 对副本执行严格只读 plan

```powershell
python backend/v3_db_upgrade.py plan `
  --database "D:\IoT-IDS-audit-copies\ids-before-v3-....sqlite"
```

保存机器可读审计结果：

```powershell
python backend/v3_db_upgrade.py plan `
  --database "D:\IoT-IDS-audit-copies\ids-before-v3-....sqlite" `
  --json
```

`plan` 使用 SQLite `mode=ro&immutable=1`，只执行完整性、schema、行数和身份冲突
查询。它不会创建迁移表，不会设置持久 PRAGMA，也不会更新时间戳。若目标旁存在
`-wal`、`-shm` 或 `-journal` 文件，命令会拒绝运行；应先使用上一节的 `backup`
命令制作一致性副本，不能手工只复制主 `.db` 文件。

审计结果至少应人工检查：

- `integrity_check` 为 `ok`；
- 旧表及行数符合现场预期；
- 当前 migration 版本和待执行迁移正确；
- 对象计划中没有 `type_conflict`；
- 重复、缺失或无效 MAC/IP 已记录；
- 输出明确显示 `automatic_legacy_asset_import: false`。

### 2.3 只对演练副本执行 apply

为 apply 的修改前备份准备另一个已存在目录：

```powershell
python backend/v3_db_upgrade.py apply `
  --database "D:\IoT-IDS-audit-copies\ids-before-v3-....sqlite" `
  --backup-directory "D:\IoT-IDS-apply-backups"
```

JSON 输出：

```powershell
python backend/v3_db_upgrade.py apply `
  --database "D:\IoT-IDS-audit-copies\ids-before-v3-....sqlite" `
  --backup-directory "D:\IoT-IDS-apply-backups" `
  --json
```

执行顺序固定为：修改前完整性检查、SQLite backup API 备份、备份完整性检查、
单事务迁移、事务内完整性检查、提交、提交后完整性检查。任何迁移语句失败都会
回滚该事务并保留已验证备份。

## 3. 检查演练结果

升级成功后，再对已升级副本执行 `plan`。预期结果：

- 当前 migration 版本为 `9`；该版本独立于 REST/API 的 `v3` 名称；
- `pending_migrations` 为空；
- `v3_schema_migrations` 有 `device_state_foundation`、`mqtt_heartbeat_sessions`、
  `realtime_event_log`、`device_lifecycle_management`、
  `device_traffic_aggregation`、`mobile_pairing_and_scoped_sessions` 和
  `mobile_user_administration`、`incident_and_mobile_notice_workflow` 和
  `unknown_device_discovery` 共九条记录；
- 四张设备状态表存在；
- `v3_mqtt_boot_sessions` 和 `v3_mqtt_device_cursors` 两张 MQTT 会话表存在；
- `v3_realtime_events` 追加式事件表及两个查询索引存在；
- `v3_device_profiles` 含档案版本、来源、重要性和退役字段，且
  `v3_device_management_audit` 追加式管理审计表存在；
- v5 流量聚合表与 v6 移动范围、配对、会话、refresh 历史、安全审计及限流表存在；
- v7 `v3_mobile_user_profiles` 移动用户扩展档案表及状态索引存在；
- `v3_device_profiles` 为零行；旧 `assets` 行数未变化；
- v9 候选隔离表及状态、MAC、时间、来源、认领设备索引存在；
- 重复执行 `apply` 时版本 `1`～`9` 出现在 `skipped_versions`，不会重新执行。

本轮绝不把旧 `assets` 转成 v3 设备。重复 IP 或 MAC 只进入审计报告，不能据此
自动生成 `device_id`、合并记录或认领物理设备。

## 4. 使用备份恢复

不要直接覆盖仍被 Flask、探针或其他进程打开的数据库。恢复演练应采用以下步骤：

1. 停止所有数据库写入者并记录故障库、`-wal`、`-shm` 的路径和时间。
2. 保留故障库，不在原路径上直接覆盖；先选择一个新的恢复目标路径。
3. 使用 SQLite shell 的 `.restore` 或 Python `sqlite3.Connection.backup()`，从本命令
   生成的备份恢复到新目标文件，不能使用只复制主文件的方式处理 WAL 数据库。
4. 对新目标执行 `PRAGMA integrity_check`，再运行本命令的 `plan` 核对表和行数。
5. 由现场负责人确认后，在维护窗口内切换数据库路径；原库继续只读保留。

如果现场没有 SQLite shell，应编写一次性、经复核的 Python 恢复脚本：源连接指向
备份文件，目标连接指向一个不存在的新路径，调用 `source.backup(target)`，关闭两端
后再进行完整性和行数核对。不要让恢复脚本导入 Flask 应用。

## 5. 禁止操作真实库的条件

以下任一项未满足时，禁止对真实数据库执行 `apply`：

- 尚未确认真实数据库的绝对路径、负责人和维护窗口；
- Flask、抓包、后台任务或其他写入者尚未停止；
- 未完成一次“制作副本 → plan → apply → 再 plan → 恢复”的全流程演练；
- 源库或演练副本的 `integrity_check` 不是 `ok`；
- 备份目录空间、访问权限、异机/异盘保留策略未确认；
- plan 出现迁移登记 checksum 冲突、已登记 schema 漂移或对象类型冲突；
- 没有指定现场操作人、复核人和失败后的恢复负责人；
- 没有记录旧资产的 MAC/IP 冲突审计结果。

稳定 `device_id`、真实 MAC 和区域归属未确认前，可以只创建空的 v3 schema，但
仍然禁止执行任何旧资产导入。

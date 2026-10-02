# 阶段 1：v3 设备状态数据基础

## 范围

本里程碑只提供可独立测试的数据表和状态服务，不注册 Flask 路由，也不接入
MQTT、SSE、虚拟设备、Web、APP 或 GNN。`backend/v3_database.py` 的初始化函数
要求调用方显式传入 SQLite 路径，因此不会隐式打开 `backend/data/ids.db`。

## 数据表

| 表 | 用途 | 关键约束 |
|---|---|---|
| `v3_device_profiles` | 稳定设备档案 | `device_id` 主键；`identity_kind + identity_value` 唯一；IP 不在档案中 |
| `v3_device_current_state` | 每台设备的当前连接状态 | 与档案一对一；保存最新 IP、接收时间、观测引用和单调 `state_version` |
| `v3_device_state_observations` | 追加式状态观测 | 每条记录保留设备自报时间和后端接收时间；当前状态只按后端接收时间判断 |
| `v3_system_component_health` | 系统组件健康 | 保存 `warming_up/ready/degraded`、重启时间、就绪时间、原因和版本 |

四张表均使用 `v3_` 前缀和 `CREATE TABLE/INDEX IF NOT EXISTS`，不修改、重命名
或删除任何旧表。`v3_schema_migrations` 记录迁移版本、名称、checksum 和应用时间；
初始化可重复执行，已经登记且 checksum 一致的迁移会跳过。

显式预览、SQLite backup API 备份、事务升级和恢复流程见
`docs/rebuild/v3-database-upgrade.md`。

## 设备身份与 IP

- `device_id` 遵循阶段 0 契约，是跨表和未来 API 使用的稳定 ID。
- `identity_kind + identity_value` 表示不可变设备身份；MAC 地址会规范化为大写冒号格式。
- 同一个 `device_id` 不能改绑其他身份，同一个身份也不能绑定多个 `device_id`。
- 每次状态观测都必须携带匹配的身份；IP 只作为可变化属性写入观测表和当前状态。

## 状态转换

连接状态使用注入时钟的后端接收时间计算：

```text
首次有效观测 -> online
距最后接收 < 15 秒 -> online
距最后接收 >= 15 秒且 < 30 秒 -> stale
距最后接收 >= 30 秒 -> offline
```

未收到任何观测的已登记设备保持 `unknown`。`active/maintenance/disabled` 与连接
状态正交：维护或停用设备仍可真实显示离线，但只有 `active + offline` 具备离线
告警资格。

系统组件启动或重启时进入 `warming_up`；完成依赖加载后由调用方明确设置为
`ready`，无法提供完整能力时设置为 `degraded` 并记录原因。重启会清空上一次
`ready_at`，避免把旧进程的就绪状态继承给新进程。

## 测试隔离

`backend/tests/test_device_state_foundation.py` 的每个服务实例都使用 pytest
`tmp_path` 下的 SQLite 文件和可推进的假时钟。测试不等待真实时间，也不创建、
打开或迁移 `backend/data/ids.db`。

升级命令测试同样只使用 `tmp_path`，包括测试生成的旧库、WAL 数据库和故障迁移。

# v3 设备档案与生命周期管理 API

## 1. 数据库 migration version 4

迁移名为 `device_lifecycle_management`，checksum 为
`685caf41c5d21471c14ef7b6608cce6d43a4cd69fff3961c1888c3166a4bebcd`。version 1、2、3
的语句与 checksum 未修改。

`v3_device_profiles` 新增：

- `importance`：`low`、`normal`、`high`、`critical`；
- `profile_source`：已有档案默认 `unclassified`，API 新建设备只接受 `physical`、
  `virtual`、`gateway`；
- `profile_version`：从 1 开始的乐观并发版本；
- `retired_at` 和 `retirement_reason`。

新增 `v3_device_management_audit` 追加式审计表，记录设备、动作、操作者 ID/用户名/角色、
UTC 时间、request_id 和修改前后 JSON。该表不对设备档案建立外键，因此设备合法彻底
删除后审计仍保留。审计不保存密码、MQTT 凭据、Cookie 或令牌。迁移不导入旧
`assets`。

数据库必须用显式命令升级：

```powershell
python backend/v3_db_upgrade.py plan --database "D:\path\copy.sqlite"
python backend/v3_db_upgrade.py apply --database "D:\path\copy.sqlite" `
  --backup-directory "D:\path\backups"
```

应用请求不会创建数据库或执行迁移。

## 2. 权限和 CSRF

| 接口 | admin | operator | user | 匿名 |
|---|---|---|---|---|
| `GET /api/v3/devices` | 读 | 读 | 403 | 401 |
| `GET /api/v3/devices/{device_id}` | 读 | 读 | 403 | 401 |
| POST/PATCH/DELETE 写接口 | 写 | 403 | 403 | 401 |

管理写接口使用 Cookie session，因此要求与当前登录身份和 Flask secret 绑定的 HMAC
CSRF token。登录后先请求任一设备 GET 接口，从响应头读取 `X-CSRF-Token`，再以同名
请求头发送写操作；token 不放 URL。token 无状态生成，GET 不写设备数据或 session。
响应同时携带 `X-Request-ID`，错误使用统一信封：

```json
{
  "error": {
    "code": "profile_version_conflict",
    "message": "device profile version is stale",
    "request_id": "..."
  }
}
```

JSON 请求上限为 16 KiB，只接受 `application/json`、UTF-8 JSON 对象和明确允许字段。

## 3. 请求与响应示例

创建设备不会写管理员提供的 IP，也不会伪造 online：

```http
POST /api/v3/devices
Content-Type: application/json
X-CSRF-Token: <GET 响应提供的 token>

{
  "device_id": "camera-01",
  "mac": "AA:BB:CC:DD:EE:01",
  "display_name": "客厅摄像头",
  "device_type": "camera",
  "area_id": "living-room",
  "importance": "high",
  "profile_source": "physical"
}
```

成功返回 201，设备初始 `connection_status` 为 `unknown`、`state_version` 为 0、
`profile_version` 为 1。修改档案或运行模式必须提供当前版本：

```json
{
  "display_name": "玄关摄像头",
  "expected_profile_version": 1
}
```

同一次 PATCH 不能混合档案字段和 `operation_mode`。退役请求包含原因和期望版本；响应
明确返回 `credential_revocation_required: true`。恢复清除退役字段、恢复 active，但不
改连接状态，并返回 `credential_reverification_required: true`。

列表支持 `search`、`connection_status`、`operation_mode`、`area_id`、`retired`、`limit`
和 `offset`，按 `device_id` 稳定排序；`limit` 为 1–100。详情返回档案、MAC、当前连接、
最近观测、来源、版本、生命周期、引用计数、`can_delete` 和删除阻止原因，不返回凭据。

## 4. 彻底删除边界

DELETE 必须发送精确匹配当前 `display_name` 的 JSON `confirmation`。以下任一证据存在时
返回 `409 device_has_history`：

- 状态观测；
- MQTT boot session 或当前 cursor；
- 非设备管理类实时事件；
- 当前或未来 graph、incident、traffic 等含 `device_id` 的引用表。

当前状态占位行、管理审计和 `device.inventory_changed` 不阻止误添加设备删除。删除不使用
级联清除历史；若出现尚未识别的外键，操作回滚并返回冲突。成功删除后 profile 与当前
状态占位行消失，管理审计和 created/deleted inventory 事件保留；deleted 事件使用
删除前版本加一作为墓碑 `profile_version`，保持生命周期版本单调递增。

## 5. 实时清单事件

每个成功写操作在档案修改的同一 SQLite 事务中写审计和
`device.inventory_changed`：

```json
{
  "event_id": 42,
  "event_type": "device.inventory_changed",
  "occurred_at": "2026-09-20T08:30:00Z",
  "device_id": null,
  "state_version": 3,
  "payload": {
    "action": "retired",
    "device_id": "camera-01",
    "profile_version": 3
  }
}
```

action 为 `created`、`updated`、`operation_mode_changed`、`retired`、`restored` 或
`deleted`。失败、冲突和重复请求不会产生成功审计或事件。现有 Web monitor 识别该事件
后关闭旧 EventSource，并把连续事件合并为一次去抖、限频的完整快照同步。

# v3 APP 配对、受限会话与设备范围

## 1. 安全边界

Web 管理员继续使用 Flask Cookie session。移动 APP 使用独立的 opaque Bearer
access token 和 refresh token；移动 token 不写入 Cookie session，也不能被 Web 的
admin/operator 装饰器识别。移动 token 只能访问：

- `POST /api/v3/mobile/token/refresh`
- `POST /api/v3/mobile/logout`
- `GET /api/v3/mobile/session`
- `GET /api/v3/mobile/overview`

Web 管理端专用接口为：

- `GET|POST /api/v3/mobile-users`
- `GET|PATCH /api/v3/mobile-users/{user_id}`
- `GET|PUT /api/v3/mobile-users/{user_id}/scopes`
- `POST /api/v3/pairing/start`
- `GET /api/v3/mobile-sessions`
- `POST /api/v3/mobile-sessions/{session_id}/revoke`

`POST /api/v3/pairing/claim` 是匿名持码客户端的唯一入口。Bearer token 不能访问
全局 monitor、设备管理、peer IP、系统配置或移动会话管理；Web Cookie 也不能代替
Bearer token 访问 mobile overview。

当前 incident/GNN 管线未实现。overview 固定明确返回：

```json
{
  "security_capability": {
    "available": false,
    "reason": "incident_pipeline_not_ready"
  }
}
```

这不代表“安全”“无攻击”或检测正常。

## 2. Migration v6

迁移名为 `mobile_pairing_and_scoped_sessions`，checksum 为
`f1ce25c5393381750c7582eb7dc783c7625db80a0dad483c1e46cf1b7521b61d`。
它新增：

- `v3_mobile_scope_sets`：每个用户的当前 scope version；
- `v3_mobile_user_scopes`：追加式 device/area 范围及撤销时间；
- `v3_mobile_pairings`：一次性配对记录，只保存 selector 和 HMAC hash；
- `v3_mobile_sessions`：客户端会话及当前 access/refresh token hash；
- `v3_mobile_refresh_history`：已轮换 refresh token 的重放检测；
- `v3_mobile_security_audit`：范围、配对、轮换、注销和撤销审计；
- `v3_mobile_rate_limits`：跨进程共享的数据库限流窗口。

迁移不导入旧用户、不创建默认用户，也不改变 migration v1～v5。升级仍只能通过
`backend/v3_db_upgrade.py plan/apply` 显式执行。

### Migration v7：移动用户管理

迁移名为 `mobile_user_administration`，checksum 为
`5e8e572496607b58d0ccf93be0bcd1deaaa7d3935f93cef54cccd35e905b3623`。
它新增 `v3_mobile_user_profiles`，保存 display name、mobile-only 标记、账号状态、
profile version、创建者以及禁用时间/原因；用户名、role 和 password hash 仍只来自
legacy `users` 表，密码没有复制到 v3。

新建 mobile-only 用户的 legacy role 固定为 `user`。服务端为 legacy password hash
生成不可返回、不可预测的随机秘密，但 Web 登录还会显式检查 `mobile_only` 并拒绝，
不能把“用户不知道秘密”当成登录边界。系统不创建默认用户，也没有把普通用户提升为
admin/operator 的 API。

禁用用户在同一事务中撤销全部有效移动 session、使全部未使用配对码失效并保留 scope
和安全审计。恢复仅恢复账号资格，不复活旧 session，必须重新配对。

## 3. Web 移动用户管理

只有 Web admin Cookie session 可以使用以下接口；写操作继续要求从已认证 GET 响应头
获得的 `X-CSRF-Token`：

```http
GET /api/v3/mobile-users?search=resident&account_status=active&mobile_only=true&limit=50&offset=0
POST /api/v3/mobile-users
GET /api/v3/mobile-users/42
PATCH /api/v3/mobile-users/42
```

创建请求只接受 username 和 display name：

```json
{"username": "resident-a", "display_name": "A 栋住户"}
```

role、密码和管理员权限字段会作为未知字段拒绝。重复用户名返回
`409 mobile_username_conflict`。新账号默认 scope 为空，因而不能生成配对码。
修改 display name 或 active/disabled 状态必须携带 `expected_profile_version`；
版本冲突返回 `409 mobile_user_profile_version_conflict`，客户端不得自动重试覆盖。

列表返回 scope 数量、active/revoked session 数量，以及是否存在尚未使用的 pairing
和其到期时间；永远不返回 pairing code、token、hash 或随机 Web 秘密。

Web 管理页面为 `/mobile-access`，使用真实 `/api/v3/devices` 构建设备范围并从真实
设备的 `area_id` 去重生成区域选项。配对码明文只存在于当前 React state：关闭
对话框、切换用户、离开路由或到期都会清除，不写浏览器存储、URL 或日志。

## 4. 授权范围

目标账号必须已存在且角色严格为 `user`。管理员和操作员账号不能绑定为普通 APP
账号。

读取范围：

```http
GET /api/v3/mobile-users/42/scopes
Cookie: <web admin session>
```

完整替换范围：

```http
PUT /api/v3/mobile-users/42/scopes
Cookie: <web admin session>
X-CSRF-Token: <从已认证 GET 响应头取得>
Content-Type: application/json

{
  "expected_scope_version": 3,
  "scopes": [
    {"scope_kind": "device", "scope_value": "camera-01"},
    {"scope_kind": "area", "scope_value": "building-a"}
  ]
}
```

device scope 必须引用现存 v3 设备；area scope 只接受受限的 `area_id` 字符集。
重复项去重。版本不一致返回 `409 mobile_scope_version_conflict`。空范围是合法值，
其含义是看不到任何设备，绝不回退为全局访问。

每次 overview 请求都从数据库读取当前范围。area scope 动态匹配设备当前的
`area_id`；设备移动区域或管理员撤销范围后，现有 session 的下一次请求立即生效。

## 5. 一次性配对

管理员先为 role=user 账号配置至少一个有效范围，然后创建配对码：

```http
POST /api/v3/pairing/start
Cookie: <web admin session>
X-CSRF-Token: <csrf token>
Content-Type: application/json

{"user_id": 42}
```

示例响应中的值均为占位：

```json
{
  "pairing_id": "00000000-0000-4000-8000-000000000000",
  "user": {"user_id": 42, "username": "resident-a"},
  "pairing_code": "ABCD-EFGH-JKLM-NPQR-STUV-WXYZ-2345-6789",
  "expires_at": "2026-09-21T04:05:00Z"
}
```

配对码使用排除 `I/O/0/1` 的人工输入字符集，默认五分钟有效。明文只在该成功
响应返回一次；数据库保存 HMAC hash，不把明文写入审计或日志。同一用户创建新码会
使旧的未领取码失效。

APP 领取：

```http
POST /api/v3/pairing/claim
Content-Type: application/json

{
  "pairing_code": "ABCD-EFGH-JKLM-NPQR-STUV-WXYZ-2345-6789",
  "client_instance_id": "android-a1b2c3d4",
  "client_display_name": "客厅平板"
}
```

领取时忽略配对码大小写、空格和连字符。成功事务同时标记配对已领取、创建 session
并写安全审计。并发领取只有一个成功。错误、不存在、过期、失效和已使用的码统一
返回 `401 pairing_claim_rejected` 和同一公开提示，避免枚举。

成功响应：

```json
{
  "session_id": "00000000-0000-4000-8000-000000000001",
  "access_token": "<opaque access token; only returned here>",
  "refresh_token": "<opaque refresh token; only returned here>",
  "access_expires_at": "2026-09-21T04:30:00Z",
  "refresh_expires_at": "2026-10-21T04:00:00Z",
  "user": {"user_id": 42, "username": "resident-a", "role": "user"}
}
```

## 6. Token 生命周期

access token 默认 30 分钟，允许配置范围为 5～60 分钟。refresh token 默认 30 天，
允许范围为 1 小时～90 天。二者由 `secrets` 生成，数据库只保存服务端 secret
计算的 HMAC hash，秘密比较使用恒定时间比较。

刷新请求：

```http
POST /api/v3/mobile/token/refresh
Content-Type: application/json

{"refresh_token": "<opaque refresh token>"}
```

成功后 access 和 refresh token 都轮换，generation 递增，refresh 总到期时间不延长。
旧 refresh token 进入历史表；再次出现会返回 `401 refresh_token_replay`，并撤销
整个 session，包括刚轮换出的 access token。

移动请求使用：

```http
Authorization: Bearer <opaque access token>
```

access token 过期、session 撤销或用户角色不再是 `user` 时立即返回 401。注销接口
撤销当前 session，重复注销返回幂等成功。管理员也可以在
`/api/v3/mobile-sessions` 查询并撤销会话；列表不会返回 token、token hash 或配对码。

## 7. Mobile overview

```http
GET /api/v3/mobile/overview
Authorization: Bearer <opaque access token>
```

示例：

```json
{
  "generated_at": "2026-09-21T04:00:00Z",
  "user": {"user_id": 42, "username": "resident-a"},
  "devices": [
    {
      "device_id": "camera-01",
      "display_name": "门厅摄像机",
      "device_type": "camera",
      "area_id": "building-a",
      "connection_status": "online",
      "operation_mode": "active",
      "retired": false,
      "retired_at": null,
      "last_updated_at": "2026-09-21T03:59:51Z",
      "availability_status": "available"
    }
  ],
  "security_capability": {
    "available": false,
    "reason": "incident_pipeline_not_ready"
  }
}
```

overview 不返回 MAC、当前 IP、peer IP、端口、graph ID、GNN 分数/特征、MQTT 或
probe 凭据。未观测设备保持 `unknown`；退役、维护、disabled 与连接状态分开表达。

## 8. HTTPS、代理、限流与部署

生产环境必须显式设置：

```text
IOT_IDS_MOBILE_TOKEN_SECRET=<独立且至少 32 字符的随机秘密>
IOT_IDS_MOBILE_ALLOW_INSECURE_HTTP=false
IOT_IDS_MOBILE_TRUST_PROXY=false
```

可选限制项见 `.env.example`：
`IOT_IDS_MOBILE_PAIRING_TTL_SECONDS`、
`IOT_IDS_MOBILE_ACCESS_TTL_SECONDS`、
`IOT_IDS_MOBILE_REFRESH_TTL_SECONDS`、
`IOT_IDS_MOBILE_PAIRING_MAX_ATTEMPTS`、
`IOT_IDS_MOBILE_RATE_WINDOW_SECONDS`、
`IOT_IDS_MOBILE_RATE_BLOCK_SECONDS`、
`IOT_IDS_MOBILE_CLAIM_RATE_LIMIT` 和
`IOT_IDS_MOBILE_REFRESH_RATE_LIMIT`。

默认不信任 `X-Forwarded-Proto` 或 `X-Forwarded-For`。只有部署在已确认会覆盖并
清理这些头的可信反向代理后，才设置 `IOT_IDS_MOBILE_TRUST_PROXY=true`。开发时
只有显式开启 insecure HTTP，且请求来自 loopback 或测试环境才允许明文，并会输出
不包含秘密的安全警告；生产配置拒绝开启。

pairing claim 和 refresh 使用 SQLite 持久限流，因此多进程共享同一限制状态；配对
记录本身还有限定尝试次数。远端地址以服务端 HMAC 后的 bucket key 保存，不保存
明文 IP。错误响应和日志不回显 Authorization、token、配对码或完整请求 payload。

禁止记录或保存：明文配对码、access/refresh token、token hash 的 API 输出、密码、
Cookie、MQTT 凭据、probe token 和完整 Authorization header。

# Web 实时监视工作区

## 数据来源与访问

管理员和值守人员登录后进入 `/monitor`。页面只使用同源相对路径：

```text
GET /api/v3/monitor
GET /api/v3/events?after=<event_cursor>
```

浏览器会携带现有 cookie session，不保存后端地址、数据库路径或 MQTT 配置。开发模式
继续由 Vite 把 `/api` 代理到本机 Flask `http://localhost:5000`；生产环境应让 Web 和 API
处于同一站点或由反向代理保持同源 cookie 语义。

## 同步流程

1. 首次进入先校验完整 monitor 快照；成功前不创建 `EventSource`。
2. 使用快照的持久 `event_cursor` 建立唯一 SSE 连接。
3. 增量事件必须连续递增，并且设备/组件的 `state_version` 必须高于本地版本；重复、倒序
   或旧版本不会覆盖新状态。
4. `snapshot.required`、事件缺口、无法解析的增量、页面从隐藏恢复或人工重试会关闭旧连接，
   重新读取完整快照，再从新游标建连。
5. 连接断开时保留最后一次真实快照并明确标记可能过期；重连使用有上限的退避，且同一
   页面不会并行创建连接。
6. 页面卸载或退出登录会关闭 `EventSource`，React StrictMode 重复执行 effect 也不会留下
   孤立连接。

`device.connection_changed` 和 `device.telemetry_updated` payload 只包含 monitor 所需的当前
连接投影：连接状态、IP、观测/接收时间、来源和状态版本。`system.component_changed` 包含
组件 readiness 投影。MAC、凭据和完整遥测正文不会进入 SSE。

## 显示原则

- 401、403、503、网络失败和契约格式错误分别显示，不用演示数据覆盖错误；
- 空设备或空组件数组是合法真实状态，不推导成“系统正常”；
- graph/incident capability 不可用时展示后端 reason，不绘制假拓扑，也不把缺少事件能力
  表述为“暂无攻击”；
- SSE 断开时设备计数和状态保持在最后一次真实值，并显示断线与最后同步时间。

## 本地验证

```powershell
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

启动已显式配置数据库的 Flask 服务后，再运行 `npm --prefix frontend run dev`。数据库必须
已通过 `backend/v3_db_upgrade.py apply` 完成显式升级；前端不会创建或迁移数据库。

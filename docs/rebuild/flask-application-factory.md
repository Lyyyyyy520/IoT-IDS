# Flask 应用工厂与运行时服务

## 1. 无副作用应用创建

正式工厂为 `backend/app.py::create_app()`。导入模块或调用工厂只会：

- 创建 Flask app，并应用可覆盖配置；
- 为该 app 创建独立的 `BackendServiceContainer`；
- 注册 health/auth、probe 及各个独立 v3 Blueprint；
- 注册缺库时的安全错误处理。

它不会创建或打开数据库、执行 migration、创建用户、加载 Paho、连接 Broker 或启动
线程。多个测试 app 拥有不同的 service container，不共享 MQTT subscriber 或流量聚合窗口。

`legacy_api` Blueprint 只保留 `/api/health` 与 `/api/auth/login|logout|me`；旧版页面所用
REST handler 已删除。`probe` Blueprint 保留实际边缘客户端使用的注册、心跳、v2 push、
状态上报及受控轮询/控制。v3 blueprint 提供 monitor/events、设备/发现、设备流量、事件、
移动用户管理、配对、受限 Mobile API 和只读系统健康；完整路径见
[`docs/05-api-spec.md`](../05-api-spec.md)。数据库缺失时数据库接口返回 503，不返回演示数据。

## 2. 唯一数据库路径

运行前必须设置：

```powershell
$env:IOT_IDS_DATABASE_PATH = "D:/path/to/verified/iot-ids.sqlite"
```

legacy 数据库助手从当前 Flask app 读取该值，并用 SQLite URI `mode=rw` 打开，所以
文件不存在时不会被 SQLite 静默创建。v3 服务和 MQTT 状态服务使用同一个规范化绝对
路径。`create_app({"DATABASE_PATH": temporary_path})` 可用于测试依赖注入。

正常启动绝不调用 `init_db()` 或 `initialize_v3_database()`。migration 只能使用
`v3_db_upgrade.py plan/apply` 显式执行。`/api/health` 始终可访问，并分别报告：

- 文件是否可打开；
- legacy 核心表是否就绪；
- migration 1～9 及 v3 对象是否完整。

任一数据库条件不满足时整体 `status` 为 `degraded`。

## 3. 正式启动与停止

兼容启动命令：

```powershell
python backend/app.py
```

也可以进入 backend 后执行 `python app.py`。入口顺序固定为：

1. `create_app()`；
2. `validate_runtime_configuration(app)`，只读检查数据库；
3. `start_runtime_services(app)`；
4. 运行 Flask；
5. 在 `finally` 中执行 `stop_runtime_services(app)`。

直接使用 `flask --app app:create_app run` 只适合不需要后台 subscriber 的 HTTP 调试，
因为应用工厂按设计不会偷偷启动运行时服务。

## 4. MQTT 生命周期

`start_runtime_services(app)` 只有在以下条件全部满足时才构造 subscriber：

- MQTT 配置明确 `enabled=true` 且配置合法；
- 调用来自正式运行入口；
- 当前不是 Flask debug reloader 父进程；
- 显式数据库文件存在且可读；
- migration 1～9 的名称和 checksum 与代码一致，全部 v3 对象存在。

启动和停止均幂等。创建或启动失败时只记录异常类型和稳定原因码，随后调用 subscriber
的 `stop()` 清理可能已经启动的网络循环和工作线程；密码不进入日志。

当前 MQTT subscriber 只能有一个。多进程 WSGI 部署时，禁止每个 Web worker 都调用
`start_runtime_services`。应运行一个独立 subscriber worker，或由进程管理器明确指定
唯一进程负责 MQTT；Web worker 只调用 `create_app()`。

## 5. Web 开发连接

Vite 开发服务器已经把 `/api` 代理到 `http://localhost:5000`。先按上述方式启动后端，
再执行：

```powershell
npm --prefix frontend run dev
```

Web 继续使用相对路径和 cookie session，不需要在浏览器中硬编码数据库路径或 MQTT
凭据。接入 v3 时先请求 `/api/v3/monitor`，再连接
`/api/v3/events?after=<event_cursor>`。

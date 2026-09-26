# IoT IDS — 实时设备安全监视

当前版本以 v3 设备监视为唯一产品主线。Web 管理端提供实时设备状态、设备发现与生命周期管理、设备详情内流量、事件处置、系统健康和移动用户授权；Expo 移动端面向普通用户，提供本人设备、提醒、设备详情、配对、求助与设置。

## 当前能力

- Web：`/monitor` 实时监视、`/devices` 设备与流量、`/incidents` 事件与移动通知、`/system-health` 健康、`/mobile-access` 移动用户管理。
- 权限：Web 使用 admin/operator 会话；普通 user 通过受限配对令牌使用 Expo APP。服务端按用户设备/区域范围裁剪数据。
- 后端：Flask `/api/health`、登录会话、probe 注册/心跳/状态/控制、v3 设备/MQTT 状态、SSE 推送、发现、流量、事件、移动通知和配对 API。
- 数据：设备、事件、流量聚合、操作审计、移动安全审计和旧版历史表均保留。应用正常启动不会创建数据库、升级 schema 或清理记录。
- 空库和请求失败按真实状态展示，不注入演示设备或演示告警。

## 启动
Windows 桌面演示可运行仓库根目录的一键启动.bat。它只会复用命令行属于本项目且 /api/health 报告数据库和 v3 schema 就绪的服务；每次按端口监听进程、命令行和健康 API 实时识别服务，不保存也不依赖 PID。无法确认身份的监听进程会保持运行并报错，不会被启动器结束。

### 后端

Python 3.10+。先指定已经验证过的 v3 SQLite 数据库文件：

```powershell
$env:IOT_IDS_DATABASE_PATH = "D:/path/to/verified/iot-ids.sqlite"
$env:IOT_IDS_SESSION_SECRET = "使用安全随机值配置"
cd backend
pip install -r requirements.txt
python app.py
```

MQTT 默认关闭。显式启用时还需配置独立的 broker 订阅账号。正常服务启动只做只读健康检查；数据库升级与恢复必须依照 [数据库升级契约](docs/rebuild/v3-database-upgrade.md) 和 [运维恢复手册](docs/12-operations-and-recovery.md) 在已审核副本上显式执行。

验证 `http://localhost:5000/api/health`。缺库或 schema 未就绪时 health 返回 `degraded`，数据库接口失败关闭，不会自动建空库。

### Web

```powershell
cd frontend
npm install
npm run dev
```

Vite 将 `/api` 转发到 `http://localhost:5000`。Web 默认进入 `/monitor`；旧书签会重定向到对应新版页面。

### Expo 移动端

```powershell
cd IoT‑IDS‑Mobile
npm install
npx expo start
```

普通用户先在管理端创建移动用户和设备范围，再通过 APP 配对。Web 普通 user 页面会提示使用移动端。

### 边缘探针和训练

Flask 主机依赖与边缘/训练依赖分开安装：`backend/requirements.txt`、`edge/requirements.txt` 和 `training/requirements.txt`。探针采用有 batch/sample ID 的 v2 信封；服务端只将白名单字段汇入 v3，并且不存原始报文载荷。当前研究模型文件和训练数据不在本次清理中删除。

## API 与文档

- [当前 API 目录](docs/05-api-spec.md)
- [v3 清理与迁移清单](docs/rebuild/legacy-cleanup-inventory.md)
- [v3 状态与 API 契约](docs/rebuild/state-and-api-contract.md)
- [设备流量契约](docs/rebuild/v3-device-traffic.md)
- [数据库升级与恢复](docs/rebuild/v3-database-upgrade.md)、[运维恢复手册](docs/12-operations-and-recovery.md)
- [重构取舍原案](docs/11-rebuild-plan.md)：第 12 节作为旧功能取舍参考；其余内容是历史设计输入，不代表当前已实现能力。

`docs/01`–`docs/10` 中过时的旧仪表盘、PCAP/Excel、策略、拓扑和部署方案均标记为历史参考。当前产品状态以本 README、`docs/05-api-spec.md` 和 `docs/rebuild/` 契约为准。

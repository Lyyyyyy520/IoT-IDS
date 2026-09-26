# v3 主线旧功能清理清单

> 完成日期：2026-09-26。当前实现及引用核验结果如下。以 `docs/11-rebuild-plan.md` 第 12 节和 `docs/rebuild/` 的 v3 契约为准。此表记录仓库代码入口与调用关系，不代表真实部署数据库内容。

## Web 页面与路由

| 入口/代码 | 决策 | 依赖与处理 |
|---|---|---|
| `/monitor`、`pages/Monitor`、`features/monitor`、`api/v3Monitor` | 保留 | `/api/v3/monitor` + 持久 SSE `/api/v3/events`；设备/健康/事件均使用真实后端状态 |
| `/devices`、`pages/Devices`、`features/devices`、`api/v3Devices`、`api/v3Discovery` | 保留 | `/api/v3/devices*` 和隔离发现 API；设备详情内挂载 v3 流量详情 |
| `features/traffic`、`api/v3Traffic` | 合并并保留 | `/api/v3/devices/{id}/traffic`、`peers`；流量只从设备详情进入 |
| `/incidents`、`pages/Incidents`、`features/incidents`、`api/v3Incidents` | 保留 | v3 事件、处置时间线与移动通知共用同一事件 ID |
| `/system-health`、`pages/SystemHealth`、`api/v3SystemHealth` | 保留 | 只读健康 API；不承担数据库维护操作 |
| `/mobile-access`、`pages/MobileAccess`、`features/mobileAccess`、`api/v3MobileAccess` | 保留 | 管理移动用户、设备/区域范围、配对码和会话；仅 admin 可见 |
| `/login`、`AuthContext` | 保留 | 复用 `/api/auth/login|logout|me`、Cookie session 与 `users`/`audit_logs`；Web 普通 user 转到仅移动端说明页 |
| `/dashboard`、`/alerts`、`/assets`、`/traffic`、`/analysis`、`/policy`、`/logs`、旧 `/settings` | 删除旧页面和 API 调用 | 旧路径只做短链重定向：设备/事件/监视/健康分别落到 v3 页面；旧流量页不再是独立入口 |
| `components/Topology`、`Heatmap`、`MitreAttack`、旧 `TrafficChart`、`RiskGauge`、`AlertCard` | 删除 | 仅旧页面引用；新流量趋势图仍用 ECharts，不能因此删除 ECharts |

## Flask API、服务和探针调用

| 接口/模块 | 决策 | 实际调用关系 |
|---|---|---|
| `GET /api/health` | 保留 | README、部署检查和外部健康探测入口；只读检查显式数据库状态，不报告文件路径 |
| `/api/auth/login|logout|me`、`services/auth.py` | 保留 | Web Cookie session、admin/operator/user 权限和 legacy 登录/审计表；v3 Web 写接口沿用该 session 与 CSRF |
| `/api/probe/register|heartbeat|push|status-report|control-status|control` | 保留并迁接 | `edge/probe_client.py`、`edge/vm_probe_client.py` 实际调用注册、状态、停止/启动轮询和上报；`push` 的 v2 batch 进入共享 v3 流量聚合，并把可定位的探针规则告警写入 v3 incident workflow |
| `/api/probe/list|status` | 删除 | 只供旧 Dashboard API wrapper/独立 probe 概览使用；当前 Web 和边缘客户端均无调用方，替代观察入口为 `/system-health` |
| 旧 Dashboard/告警/资产/拓扑/热力图/MITRE/PCAP 上传/Excel/封禁/策略/独立日志/配置路由 | 删除 | 只由已删除旧页面和旧 `frontend/src/api/index.ts` 调用；共享鉴权、审计记录、探针控制及 v3 设备生命周期不受影响 |
| `services/traffic_capture.py`、`rule_engine.py`、`device_detector.py`、`gat_detector.py` 与旧 CICIDS 流量适配器 | 删除旧运行时接入 | 调用关系只连到已删除的 capture/PCAP/rule 路径；v2 probe flow 进入 `DeviceTrafficService`，首次接受且能唯一绑定设备的规则告警进入 incident workflow；不保留旧 GNN/CICIDS 运行时包装器或自动 block |
| `runtime_services.py` | 保留并精简 | 仍管理单实例 MQTT subscriber、每 app 服务容器、数据库健康；移除已无入口的 local capture 生命周期 |
| MQTT ingestion/subscriber、probe auth、`DeviceStateService`、`DeviceTrafficService`、discovery、incidents/mobile、SSE、health | 保留 | 分别由正式 Flask 生命周期、ESP32 MQTT status、edge probe v2、v3 Web/APP 路由调用 |

## SQLite 数据和迁移

本次未打开、迁移或修改任何真实部署数据库；数据库级测试仅使用 pytest 创建的临时 SQLite。不新增 migration，不改动 migration v1–v9 SQL/checksum。所有现有数据库表和记录都保留，包括已退役旧模块的数据。

| 工具 | 决策/依赖 |
|---|---|
| `v3_db_upgrade.py` plan/apply、`v3_db_maintenance.py`、`v3_admin_bootstrap.py` | 保留 | 继续作为显式升级、备份维护与管理员初始化工具；应用启动不调用它们，本次没有在真实库执行任何命令 |

| 表组 | 表 | 决策/依赖 |
|---|---|---|
| legacy 身份与探针兼容 | `users`, `audit_logs`, `assets`, `config` | 保留；登录/审计及边缘 probe 注册、状态、控制轮询仍有调用 |
| 旧产品历史 | `alerts`, `traffic_logs`, `policies`, `rules` | 保留真实表和所有记录；移除本次旧 Web/API 读写入口，不做 DROP/迁移 |
| v3 基础与实时 | `v3_schema_migrations`, `v3_device_profiles`, `v3_device_state_observations`, `v3_device_current_state`, `v3_system_component_health`, `v3_mqtt_boot_sessions`, `v3_mqtt_device_cursors`, `v3_realtime_events` | 保留；设备状态、MQTT 序号、健康和 SSE 事实来源 |
| v3 设备审计与流量 | `v3_device_management_audit`, `v3_device_ip_bindings`, `v3_device_traffic_minutes`, `v3_device_traffic_protocol_minutes`, `v3_device_traffic_peer_minutes`, `v3_traffic_ingest_batches`, `v3_traffic_ingest_samples`, `v3_traffic_unassigned_minutes` | 保留；设备操作审计、probe v2 去重与详情流量 |
| v3 APP/用户 | `v3_mobile_scope_sets`, `v3_mobile_user_scopes`, `v3_mobile_pairings`, `v3_mobile_sessions`, `v3_mobile_refresh_history`, `v3_mobile_security_audit`, `v3_mobile_rate_limits`, `v3_mobile_user_profiles` | 保留；用户范围、配对、token/session 安全和移动用户管理 |
| v3 事件/发现 | `v3_incidents`, `v3_incident_devices`, `v3_incident_timeline`, `v3_mobile_notice_acknowledgements`, `v3_mobile_notice_changes`, `v3_help_requests`, `v3_help_request_timeline`, `v3_support_contacts`, `v3_incident_workflow_audit`, `v3_discovered_device_candidates`, `v3_discovery_observations`, `v3_discovery_actions` | 保留；事件证据、移动提醒/求助、未知设备隔离发现和审计 |

`backend/data/` 内 ONNX、归一化、pickle 与 `.pt` 模型文件，以及 `training/` 脚本均保留；edge live path 仍使用 ONNX 推理，但占位特征产生的分数只打印到本地终端，不创建 v3 incident。删除了无调用方的 `seed_demo.py`、旧 YAML 规则包、旧 detection/auto-block 默认配置和 `database.init_db()` 中假设备自动 seed；空设备库如实显示为空。

## Expo 移动端屏幕与依赖

| 屏幕/模块 | 决策 | 依赖 |
|---|---|---|
| `src/mobile/HomeScreen`、`DevicesScreen`、`NoticeScreens`、`MobileDeviceDetailScreen`、`HelpScreens`、`PairingScreen`、`SettingsScreen`、`MobileContext`、`api`、`tokenCoordinator`、`storage` | 保留 | Expo 普通用户流程、SecureStore、scoped Mobile Bearer、本人设备/提醒/详情/配对/帮助/设置 |
| `src/screens/DashboardScreen`、`AssetsScreen`、`AlertsScreen`、`HistoryScreen`、`AnalysisScreen`、`MonitorScreen`、`AlertDetailScreen`、旧 `LoginScreen`/`SettingsScreen` | 删除 | 无导航引用；它们使用旧 `/api/dashboard|alerts|assets|logs|analysis` 和旧 admin Cookie 鉴权 |
| `src/components/*`、`src/api/*`、`src/context/AuthContext.tsx`、旧 `types.ts`、旧 `utils/format.ts`、旧根 `config.ts` | 删除 | 仅上列旧 screens 使用；新 `src/mobile/*` 有独立的 scoped client、types、配置、UI 和存储 |
| Expo/React Navigation/SecureStore/AsyncStorage/Expo Crypto | 保留 | 新普通用户主线实际 import；没有移除已使用包 |

## 依赖

| 依赖 | 决策 | 依据 |
|---|---|---|
| 前端 `d3`, `@types/d3` | 删除 | 仅被旧静态 topology/heatmap 引用 |
| 前端 `echarts`, `echarts-for-react` | 保留 | `features/traffic/TrafficTrendChart` 仍在新版设备详情实际使用 |
| 后端 `openpyxl`, `scapy`, `pyyaml` | 删除 Flask API 依赖 | 分别只服务 Excel、旧 server capture 和 YAML 规则运行时；Scapy 仍由 edge probe requirements 安装 |
| 后端 `torch`, `scikit-learn`, NumPy/Pandas/ONNX Runtime | 移出 Flask API requirements | PyTorch/数据科学包归训练；edge 模型运行依赖在 `edge/requirements.txt`，训练和验证依赖在 `training/requirements.txt` |
| 后端 `flask`, `flask-cors`, `paho-mqtt` | 保留 | Web/API、CORS 与显式 MQTT subscriber 使用 |
| Expo `expo-constants`, `react-native-svg` | 删除直接依赖 | 当前移动端代码无 import；由 Expo 模板间接要求的包仍由 npm 依赖树解析 |
| Expo、React Navigation、SecureStore、AsyncStorage、Expo Crypto | 保留 | 新普通用户主线实际 import |

依赖文件现已按运行边界拆分：`backend/requirements.txt`（Flask API）、`edge/requirements.txt`（探针与保留的 ONNX edge detector）、`training/requirements.txt`（训练和模型验证）。

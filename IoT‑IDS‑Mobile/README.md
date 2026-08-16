# 🛡️ IoT IDS Mobile — 移动端

智慧社区物联网僵尸网络入侵检测系统 **移动端 App**（Expo / React Native）。

本 App 对接原网页后端项目 `D:\IoT‑IDS\backend`（Flask + SQLite + ONNX），复刻网页端全部物联网入侵检测核心功能。项目与网页端**完全解耦**，不读取、不修改 `D:\IoT‑IDS` 下任何文件。

---

## 功能页面

| 页面 | 说明 | 对应后端接口 |
|------|------|--------------|
| 态势大屏 | KPI 统计、流量趋势图、攻击类型分布、最新告警 | `GET /api/dashboard/stats` |
| 设备列表 | IoT 设备资产列表、在线/离线/告警状态 | `GET /api/assets` |
| 数据监控 | 实时抓包检测控制、探针节点状态、实时流量日志 | `/api/capture/*`、`/api/probe/status`、`/api/traffic/logs` |
| 分析视图 | 网络拓扑、攻击热力图（7 天×24 小时）、MITRE ATT&CK 攻击链 | `/api/analysis/topology`、`/api/analysis/heatmap`、`/api/analysis/mitre` |
| 入侵告警 | 告警列表（筛选/合并）、实时轮询新告警、拉黑/溯源/误报 | `/api/alerts`、`/api/alerts/new`、`/api/alerts/:id/*` |
| 历史记录 | 告警历史（时间范围筛选）、审计日志（管理员） | `/api/alerts`、`/api/logs/audit` |
| 系统设置 | 后端地址配置、连接测试、账号信息、退出登录 | `/api/health`、`/api/config` |

---

## 技术栈

- Expo SDK 54 + React Native 0.81
- React Navigation 7（Bottom Tabs + Native Stack）
- `react-native-svg` 自绘图表
- `@react-native-async-storage/async-storage`（会话与配置持久化）
- TypeScript

---

## 与后端的对接方式

原网页后端使用 **Flask Session（签名 Cookie）** 认证，移动端没有浏览器自动 Cookie 存储，因此 App 内置了 Cookie 管理：

1. 登录成功后从响应头 `Set-Cookie` 抓取 `session=xxx`；
2. 持久化到本地存储；
3. 后续每个请求手动携带 `Cookie: session=xxx`。

**后端地址自动推导**：在 Expo Go 开发模式下，App 会从 Expo 的 `hostUri` 自动推导出开发机（电脑）的局域网 IP，拼接成 `http://<电脑IP>:5000/api`，因此真机调试**通常无需手动配置**即可连上同一台电脑上的后端。

如需手动指定，可在 App 内「系统设置」页或登录页右下角「后端连接设置」中修改，或编辑 `app.json` 的 `extra.apiBaseUrl`。

---

## 环境要求

- **Node.js >= 20**（当前环境 Node v24 ✅）
- **后端已启动**：`D:\IoT‑IDS\backend`（Flask，监听 `0.0.0.0:5000`）
- 手机端 **Expo Go** App（应用商店搜索安装），且手机与电脑处于**同一局域网**

---

## 安装依赖

在 `D:\IoT‑IDS‑Mobile` 目录下执行：

```bash
npm install
```

> 首次安装若提示 Expo 依赖版本需要校正，可执行：
> ```bash
> npx expo install --fix
> ```

---

## 启动步骤（完整）

### 第一步：启动后端（原网页项目）

另开一个终端，启动 `D:\IoT‑IDS` 的后端：

```bash
cd D:\IoT-IDS\backend
python app.py
```

看到 `Running on http://...5000` 即成功。可浏览器访问验证：

```
http://localhost:5000/api/health
```

返回 `{"status": "ok", ...}` 即后端就绪。

### 第二步：启动移动端

```bash
cd D:\IoT‑IDS‑Mobile
npx expo start
```

启动后终端会显示二维码。

### 第三步：在手机上运行

- **真机（推荐）**：手机安装 Expo Go，扫描终端二维码即可打开 App。
  - 手机与电脑需在同一 Wi-Fi 下。
  - App 会自动把后端地址指向电脑 IP 的 5000 端口。
- **Android 模拟器**：按 `a` 键（后端地址会自动推导为 `10.0.2.2:5000`，或手动在设置页改）。
- **iOS 模拟器（仅 macOS）**：按 `i` 键。

### 第四步：登录

使用后端默认账号登录：

| 账号 | 密码 | 权限 |
|------|------|------|
| `admin` | `admin123` | 管理员（可拉黑/溯源/误报/抓包控制/审计日志） |
| `guest` | `guest123` | 普通用户（只读） |

---

## 常用命令

| 命令 | 说明 |
|------|------|
| `npm install` | 安装依赖 |
| `npx expo start` | 启动开发服务器 |
| `npx expo start --android` | 直接启动 Android |
| `npx expo start --ios` | 直接启动 iOS（仅 macOS） |
| `npx expo start --tunnel` | 局域网不通时用隧道模式（需 Expo 账号） |

---

## 目录结构

```
IoT‑IDS‑Mobile/
├── App.tsx                    # 应用入口
├── app.json                   # Expo 配置（extra.apiBaseUrl 可覆盖后端地址）
├── package.json
├── tsconfig.json
├── babel.config.js
└── src/
    ├── config.ts              # API Base URL 自动推导
    ├── theme.ts               # 暗色主题 + 风险色板（对齐网页端 theme.css）
    ├── types.ts               # 后端返回结构类型定义
    ├── api/
    │   ├── client.ts          # fetch 封装 + Flask 会话 Cookie 管理
    │   └── index.ts           # 全部后端 API 方法
    ├── context/
    │   └── AuthContext.tsx    # 登录状态管理
    ├── navigation/
    │   └── index.tsx          # 导航（登录栈 + 底部 Tab）
    ├── components/            # 通用组件（卡片/徽章/图表/列表项等）
    ├── screens/               # 页面（登录/大屏/设备/监控/告警/历史/详情/设置）
    └── utils/format.ts        # 数字/时间格式化
```

---

## 注意事项

1. **同一局域网**：真机需与后端所在电脑处于同一 Wi-Fi；若无法连通，用 `--tunnel` 或手动填写后端地址。
2. **HTTP 明文流量**：开发期（Expo Go）访问 `http://` 局域网地址正常。若将来打包成独立 Android APK，需在 `app.json` 开启 `usesCleartextTraffic`（或使用 HTTPS）才能访问 `http` 后端。
3. **会话失效**：后端 `secret_key` 每次启动会随机生成，后端重启后 App 会话失效，需重新登录。
4. **管理员接口**：拉黑 IP、溯源、标记误报、抓包控制、审计日志等仅 `admin` 账号可操作；`guest` 会看到只读界面。
5. **未改动原项目**：本 App 完全独立，`D:\IoT‑IDS` 目录下的任何文件均未修改。

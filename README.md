# 🛡️ IoT IDS — 智慧社区 IoT 僵尸网络入侵检测系统

## 快速开始

### 环境要求
- **Node.js** >= 18.x（前端）
- **Python** >= 3.9（后端）
- Windows 10/11 或树莓派 OS

### 启动前端（React Web 界面）
```bash
cd frontend
npm install
npm run dev
```
浏览器打开 http://localhost:3000

### 启动后端（Flask API 服务）

后端必须指向一个已经存在、已经通过显式 v3 升级流程验证的 SQLite 文件。正常启动
不会创建数据库、执行 migration 或创建默认账号。PowerShell 示例：

```powershell
$env:IOT_IDS_DATABASE_PATH = "D:/path/to/verified/iot-ids.sqlite"
cd backend
pip install -r requirements.txt
python app.py
```

数据库副本的 `plan`、`apply` 与恢复步骤见
[`docs/rebuild/v3-database-upgrade.md`](docs/rebuild/v3-database-upgrade.md)。启用 MQTT 前
还必须配置独立的后台订阅账号；MQTT 默认关闭，只有正式入口显式启动运行时服务。

API 服务运行在 http://localhost:5000

### 验证联通
浏览器访问 http://localhost:5000/api/health

若数据库路径缺失、文件不存在或 v3 schema 未就绪，health 仍返回进程状态，但整体
状态为 `degraded`，数据库详情显示 unavailable；其他数据库接口不会创建空库。

返回 `{"status": "ok", ...}` 即成功。

---

## 项目结构

| 目录 | 说明 |
|------|------|
| [frontend/](frontend/) | React + Vite + TypeScript Web 前端 |
| [backend/](backend/) | Flask RESTful API + ONNX 推理服务 |
| [edge/](edge/) | 树莓派边缘部署脚本 |
| [training/](training/) | 模型训练脚本（数据预处理 + 训练 + 量化） |
| [docs/](docs/) | 项目标准文档（需求/技术/设计/API） |
| [dev-logs/](dev-logs/) | 开发日志 |

## 文档索引

| 文档 | 内容 |
|------|------|
| [01-requirements.md](docs/01-requirements.md) | 项目需求规格 |
| [02-tech-stack.md](docs/02-tech-stack.md) | 技术栈与架构设计 |
| [03-design-spec.md](docs/03-design-spec.md) | UI 设计规范（色板/布局/组件） |
| [04-execution-plan.md](docs/04-execution-plan.md) | 分阶段执行计划 |
| [05-api-spec.md](docs/05-api-spec.md) | API 接口规范 |


## 开发进度
- [x] 阶段一：项目脚手架与环境搭建
- [ ] 阶段二：仪表盘与告警页面
- [ ] 阶段三：分析视图
- [ ] 阶段四：检测引擎
- [ ] 阶段五：配置与收尾
- [ ] 阶段六：演示准备

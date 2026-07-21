# 🛡️ IoT IDS — 智慧社区 IoT 僵尸网络入侵检测系统

## 快速开始

### 环境要求
- **Node.js** >= 18.x（前端）
- **Python** >= 3.9（后端）
- Windows 10/11 或树莓派 OS

### Windows 一键启动（推荐）

解压后直接双击：

```text
一键启动.bat
```

第一次运行会自动创建 `.venv`、安装前后端依赖并启动服务；后续运行在依赖文件未变化时会跳过初始化，直接启动。浏览器会自动打开 `http://127.0.0.1:3000/`。

辅助脚本：

- `一键停止.bat`：停止由一键脚本启动的前端和后端。
- `重新初始化环境.bat`：删除并重建依赖环境后再启动。
- `一键启动说明.txt`：查看详细说明与日志位置。

### 启动前端（React Web 界面）
```bash
cd frontend
npm install
npm run dev
```
浏览器打开 http://localhost:3000

### 启动后端（Flask API 服务）
```bash
cd backend
pip install -r requirements.txt
python app.py
```
API 服务运行在 http://localhost:5000

### 验证联通
浏览器访问 http://localhost:5000/api/health

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

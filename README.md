# 🛡️ IoT IDS — 智慧社区 IoT 僵尸网络入侵检测系统

## 一键启动（Windows）

双击项目根目录中的 `start.bat`。

启动器会自动执行以下流程：

1. 检查 `.venv`、前端 `node_modules` 和初始化标记。
2. 已初始化且依赖文件未变化：直接启动前后端。
3. 未初始化或依赖文件有变化：自动创建 Python 虚拟环境、安装后端和前端依赖。
4. 后端运行在 `http://127.0.0.1:5000`，前端运行在 `http://127.0.0.1:3000`。
5. 两个服务就绪后自动打开浏览器。

环境要求：

- Windows 10/11
- Node.js 18 或更高版本
- Python 3.9 或更高版本，建议 Python 3.12

初始化成功后会生成 `.iot-ids-initialized.json`。只有当依赖文件发生变化时，启动器才会重新执行依赖安装。
首次安装包含 PyTorch、ONNX Runtime 等依赖，耗时取决于网络速度；安装过程中不要关闭启动窗口。

---

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

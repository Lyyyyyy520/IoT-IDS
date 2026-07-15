# 🛡️ IoT IDS — 智慧社区 IoT 僵尸网络入侵检测系统

基于轻量化深度学习（CNN+LSTM）的智慧社区 IoT 僵尸网络入侵检测系统。
天津理工大学 · 大学生创新训练计划（校级）· 2026

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

## 团队

| 成员 | 分工 |
|------|------|
| 李云锦 | 总负责人 — 方案设计、算法实现、论文撰写 |
| 谢庚泉 | 数据集与特征工程 |
| 李津涛 | 深度学习模型研发 |
| 杨明敏 | 系统前端开发 |
| 韦思杨 | 树莓派部署与测试 |
| 胡宇翔 | 成果与文档管理 |

---

## 开发进度
- [x] 阶段一：项目脚手架与环境搭建
- [ ] 阶段二：仪表盘与告警页面
- [ ] 阶段三：分析视图
- [ ] 阶段四：检测引擎
- [ ] 阶段五：配置与收尾
- [ ] 阶段六：演示准备

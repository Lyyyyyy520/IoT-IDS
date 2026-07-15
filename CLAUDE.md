# CLAUDE.md — IoT IDS 项目开发指引

## 项目概述
基于轻量化深度学习的智慧社区 IoT 僵尸网络入侵检测系统。
天津理工大学大创项目（校级），负责人：李云锦。

## 关键路径

### 标准文档
| 文件 | 路径 | 内容 |
|------|------|------|
| 需求规格 | [docs/01-requirements.md](docs/01-requirements.md) | 项目功能与非功能需求 |
| 技术架构 | [docs/02-tech-stack.md](docs/02-tech-stack.md) | 技术栈、架构图、约束条件 |
| UI 设计规范 | [docs/03-design-spec.md](docs/03-design-spec.md) | 色板、布局、组件规范 |
| 执行计划 | [docs/04-execution-plan.md](docs/04-execution-plan.md) | 六阶段任务清单 |
| API 规范 | [docs/05-api-spec.md](docs/05-api-spec.md) | RESTful 接口定义 |

### 开发日志
每日日志保存在 `dev-logs/YYYY-MM-DD.md`，记录完成事项和待办。

### 项目结构
```
iot-ids/
├── frontend/          # React + Vite + TypeScript
├── backend/           # Flask + ONNX Runtime
├── edge/              # 树莓派部署脚本
├── training/          # 模型训练脚本
├── docs/              # 项目标准文档
├── dev-logs/          # 开发日志
└── CLAUDE.md          # 本文件
```

## 开发规则

### 通用规则
1. **每次只做一个阶段**，完成并验证后再进入下一阶段
2. **每次改动后更新当天 dev-log**，记录做了什么、遇到什么问题
3. **UI 改动必须对照 [docs/03-design-spec.md](docs/03-design-spec.md)**，确保配色/布局一致
4. **新增 API 必须同步更新 [docs/05-api-spec.md](docs/05-api-spec.md)**
5. 风险色只能使用规范中定义的四种（红/橙/黄/绿），禁止自定义

### 前端规则
- 所有样式优先使用 `theme.css` 中的 CSS 变量
- 禁止在组件内写硬编码颜色
- 新页面必须放在 `pages/` 下对应目录
- 可复用可视化组件放在 `components/` 下

### 后端规则
- API 路由统一前缀 `/api/`
- 所有接口返回 JSON
- 数据库操作走 SQLite，不引入其他数据库
- 模型推理走 ONNX Runtime，不直接加载 PyTorch 模型

### 部署规则
- Windows 开发环境：`npm run dev`（前端）+ `python app.py`（后端）
- 树莓派环境：Python 3.9+, ONNX Runtime, 脚本路径 `edge/`

## 当前进度
- [x] 项目规划与文档体系建立
- [ ] 阶段一：项目脚手架与环境搭建（进行中）
- [ ] 阶段二：仪表盘与告警页面
- [ ] 阶段三：分析视图
- [ ] 阶段四：检测引擎
- [ ] 阶段五：配置与收尾
- [ ] 阶段六：演示准备

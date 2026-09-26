# CLAUDE.md — IoT IDS 项目开发指引

## 项目概述
基于轻量化深度学习的智慧社区 IoT 僵尸网络入侵检测系统。
天津理工大学大创项目（校级），负责人：李云锦。

## 关键路径

### 标准文档
| 文件 | 路径 | 内容 |
|------|------|------|
| 当前功能/API | [README.md](README.md)、[docs/05-api-spec.md](docs/05-api-spec.md) | 当前 v3 产品范围与实际路由 |
| v3 契约 | [docs/rebuild/](docs/rebuild/) | 实时设备、流量、事件、移动端与运行时契约 |
| 运维恢复 | [docs/12-operations-and-recovery.md](docs/12-operations-and-recovery.md) | 显式升级、备份、维护与恢复 |
| 清理记录 | [docs/rebuild/legacy-cleanup-inventory.md](docs/rebuild/legacy-cleanup-inventory.md) | 旧功能入口、调用关系、保留/合并/删除决策 |
| 历史计划 | [docs/11-rebuild-plan.md](docs/11-rebuild-plan.md) | 第 12 节旧功能取舍参考；其他段落为历史设计输入 |

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

## 当前产品主线
- Web admin/operator：实时设备监视、设备管理/发现/流量、安全事件、系统健康、移动用户授权。
- Expo 普通用户：本人设备、事件提醒、设备详情、配对、求助和设置。
- Flask：v3 API、MQTT 与 probe v2 上报、持久 SSE、事件/移动通知、会话鉴权、审计。
- 数据库升级、恢复和维护均通过显式 CLI；普通应用启动只读检查，不创建/升级/清理数据库。
- 旧版页面和 API 不再注册；历史文档页已标明参考状态，清单见 `docs/rebuild/legacy-cleanup-inventory.md`。

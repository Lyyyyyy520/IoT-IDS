# 技术栈与架构设计

## 1. 技术选型

| 层级 | 技术 | 版本 | 用途 |
|------|------|------|------|
| 前端框架 | React + Vite + TypeScript | 19 + 6 | 组件化 Web 界面 |
| UI 库 | Ant Design | 5.x | 暗色主题、导航、表格、表单 |
| 图表 | ECharts + D3.js | 5.x + 7.x | 常规图表 + 网络拓扑力导向图 |
| 样式 | CSS Variables | — | 全局主题变量（无第三方依赖） |
| 路由 | React Router | 7.x | 四页面导航 |
| 后端 | Flask | 3.x | RESTful API 服务 |
| 深度学习 | PyTorch → ONNX Runtime | 2.x | 训练 + 跨平台推理 |
| 流量处理 | Scapy + Pandas | 2.x | 抓包解析 + 数据处理 |
| 数据库 | SQLite | 3.x | 本地告警/日志存储 |

## 2. 架构图

```
┌───────────────────────────────────┐
│           Browser (Windows)        │
│  ┌─────────────────────────────┐  │
│  │  React SPA                  │  │
│  │  /dashboard /alerts         │  │
│  │  /analysis  /settings       │  │
│  └──────────┬──────────────────┘  │
│             │ HTTP (localhost:5000)│
│  ┌──────────▼──────────────────┐  │
│  │  Flask API Server           │  │
│  │  /api/health                │  │
│  │  /api/detect  (POST pcap)   │  │
│  │  /api/alerts  (CRUD)        │  │
│  │  /api/dashboard (stats)     │  │
│  └──────────┬──────────────────┘  │
│             │                      │
│  ┌──────────▼──────────────────┐  │
│  │  ONNX Inference Engine      │  │
│  │  best_model.onnx            │  │
│  │  scaler.pkl                 │  │
│  └─────────────────────────────┘  │
└───────────────────────────────────┘
```

## 3. 关键约束
- **不装数据库服务**：SQLite 单文件，`backend/data/ids.db`
- **不依赖外网**：全部本地运行（除了 npm install）
- **不装 GPU 驱动**：模型推理走 CPU（ONNX 优化够快）
- **前端不写 CSS-in-JS**：统一用 `theme.css` 变量

# 分阶段执行计划

## 阶段总览

```
阶段一 → 阶段二 → 阶段三 → 阶段四 → 阶段五 → 阶段六
脚手架   仪表盘   分析视图  检测引擎  配置收尾  演示准备
2-3天    3-4天    3-4天    4-5天    2-3天    1-2天
```

---

## 阶段一：项目脚手架与环境搭建

### 目标
深色界面框架 + 四页可切换导航 + 前后端联通

### 任务清单
- [ ] React + Vite + TypeScript 项目创建
- [ ] Ant Design 暗色主题配置
- [ ] 左侧导航组件（Dashboard / Alerts / Analysis / Settings）
- [ ] 顶部筛选栏框架
- [ ] React Router 四页面路由
- [ ] `theme.css` 全局样式及风险色变量
- [ ] Flask 项目骨架 + CORS
- [ ] `/api/health` 接口联通验证

### 产出物
- `frontend/src/layouts/MainLayout.tsx`
- `frontend/src/App.tsx`
- `frontend/src/styles/theme.css`
- `backend/app.py`

---

## 阶段二：仪表盘与告警页面

### 目标
核心数据页面可用，Mock 数据驱动

### 任务清单
- [ ] 仪表盘 KPI 卡片组件
- [ ] ECharts 流量曲线图
- [ ] ECharts 攻击分布饼图
- [ ] 最近告警列表（滚动）
- [ ] 告警表格（Ant Design Table）
- [ ] 高危行置顶 + 红色标识
- [ ] 多维筛选器（时间/类型/等级/设备/IP）
- [ ] 后端告警合并逻辑
- [ ] 快捷操作按钮（拉黑/溯源/标记误报）

### 产出物
- `frontend/src/pages/Dashboard/index.tsx`
- `frontend/src/pages/Alerts/index.tsx`
- `backend/api/dashboard.py`
- `backend/api/alerts.py`
- `backend/services/alert_manager.py`

---

## 阶段三：分析视图页面

### 目标
四大可视化组件全部可用

### 任务清单
- [ ] 网络拓扑图（D3.js 力导向图）
- [ ] 攻击热力图（ECharts 热力日历）
- [ ] 流量图表（ECharts 折线/面积图）
- [ ] MITRE ATT&CK 链路视图（横向流程图）

### 产出物
- `frontend/src/components/Topology/index.tsx`
- `frontend/src/components/Heatmap/index.tsx`
- `frontend/src/components/TrafficChart/index.tsx`
- `frontend/src/components/MitreAttack/index.tsx`
- `frontend/src/pages/Analysis/index.tsx`

---

## 阶段四：检测引擎集成

### 目标
真实模型可推理，端到端检测可用

### 任务清单
- [ ] CNN+LSTM 模型定义（PyTorch）
- [ ] 21 维特征提取流水线
- [ ] 训练脚本 + 数据预处理
- [ ] ONNX 模型导出
- [ ] Flask 推理接口封装
- [ ] 前端上传 PCAP + 展示结果

### 产出物
- `backend/models/cnn_lstm.py`
- `backend/models/inference.py`
- `backend/services/feature_extract.py`
- `backend/api/detect.py`
- `training/preprocess.py`
- `training/train.py`

---

## 阶段五：配置页面与系统收尾

### 目标
所有辅助功能完备，流程闭环

### 任务清单
- [ ] 配置页面：检测模式/模型信息/阈值
- [ ] 历史记录查询
- [ ] Excel 导出
- [ ] 树莓派部署脚本
- [ ] Systemd 服务配置
- [ ] Bug 修复 + 全链路联调

---

## 阶段六：演示准备

### 目标
演示材料完备

### 任务清单
- [ ] 预置 Demo 样本数据
- [ ] 界面截图
- [ ] 演示视频录制
- [ ] README 文档
- [ ] 答辩 PPT 素材整理

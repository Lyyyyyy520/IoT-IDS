# CNN + LSTM 模型导入与修复

## 推荐操作

1. 双击项目根目录的 `导入CNN_LSTM模型.bat`。
2. 首次运行或依赖报错时，双击 `重新初始化环境.bat`。
3. 再运行 `一键启动.bat`。
4. 在“系统配置 → 模型管理”确认 `best_model.ts` 显示为“已加载”。

## 模型必须成套导入

以下文件缺一不可：

```text
backend/data/best_model.ts
backend/data/best_model.pt
backend/data/feature_schema.json
backend/data/scaler.json
backend/data/scaler.pkl
```

`best_model.ts` 是推荐运行文件；`best_model.pt` 是训练 checkpoint。修正版同时支持 `.ts`、`.pt/.pth` 和 `.onnx`，并在导入时执行一次真实推理校验。无效文件会被撤销，不会留在模型列表中。

## 已修复的问题

- 修复旧界面只允许上传 `.onnx` 的问题。
- 支持导入本项目的 `.pt/.pth` checkpoint。
- 同时兼容 `(batch, 16, 21)` 新模型和 `(batch, 21)` 旧 ONNX 模型。
- 修复 `model_config.json` 保存旧绝对路径导致移动项目后加载失败的问题。
- 增加 `scaler.json`，规避不同 scikit-learn 版本读取 `scaler.pkl` 的兼容错误。
- 增加模型、特征配置和标准化器的一键校验脚本。

## 命令行校验

```powershell
.\.venv\Scripts\python.exe training\verify_model.py
```

输出 `[通过]` 才说明模型已经正确导入。

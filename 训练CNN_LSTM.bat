@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [错误] 尚未初始化项目环境。
  echo 请先运行“一键启动.bat”完成首次初始化，然后关闭系统再训练。
  pause
  exit /b 1
)

echo 请输入包含 1-9 项 ZIP 数据文件的文件夹路径：
set /p "DATA_DIR=> "
if not exist "%DATA_DIR%" (
  echo [错误] 数据文件夹不存在：%DATA_DIR%
  pause
  exit /b 1
)

echo.
echo 开始训练正式 CNN + LSTM 模型。训练结果会写入 backend\data。
"%PYTHON%" training\train.py --zip_dir "%DATA_DIR%" --epochs 12 --batch_size 512 --rows_attack 12000 --rows_benign 50000 --max_train_per_class 18000 --max_val_per_class 3000 --max_test_per_class 5000
if errorlevel 1 (
  echo.
  echo [失败] 训练中断，请查看终端错误信息。
  pause
  exit /b 1
)

echo.
echo [完成] 已生成 best_model.pt、best_model.ts、scaler.pkl 和训练报告。
pause

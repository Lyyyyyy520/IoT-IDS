@echo off
chcp 65001 >nul
title IoT-IDS One-Click Start

echo ========================================
echo   IoT-IDS IDS System
echo ========================================
echo.

echo [1/3] Starting backend ...
start "IoT-IDS Backend" cmd /k "cd /d %~dp0backend && call ..\.venv\Scripts\activate.bat && python app.py"

echo Waiting for backend ...
ping 127.0.0.1 -n 4 >nul

echo [2/3] Starting frontend ...
start "IoT-IDS Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo Waiting for frontend ...
ping 127.0.0.1 -n 6 >nul

echo [3/3] Opening browser ...
start http://localhost:3000

echo.
echo ========================================
echo  Frontend : http://localhost:3000
echo  Backend  : http://localhost:5000
echo ========================================
echo.
pause
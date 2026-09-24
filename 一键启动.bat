@echo off
chcp 65001 >nul
title IoT-IDS One-Click Demo
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-demo.ps1"
set "RESULT=%ERRORLEVEL%"
if "%RESULT%"=="0" start "" "http://127.0.0.1:3000/login"
if not "%RESULT%"=="0" echo.
if not defined IOT_IDS_STARTUP_NO_PAUSE pause
exit /b %RESULT%
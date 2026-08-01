@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo          IoT-IDS One-Click Start
echo ========================================
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_iot_ids.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [FAILED] IoT-IDS did not start. Review the error above.
    echo.
    pause
)

exit /b %EXIT_CODE%

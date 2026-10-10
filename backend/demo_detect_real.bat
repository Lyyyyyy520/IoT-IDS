@echo off
rem 真实抓包模式检测闭环（现场演示用）：后端跑在 Pi 上时 interface 用 wlan0；
rem 后端跑在 Windows 笔记本上时改为本机网卡名（如 WLAN，需先装 Npcap）。
rem 用法: demo_detect_real.bat  （会提示输入 admin 密码与网卡名）
setlocal EnableExtensions
set "BASE=http://127.0.0.1:5000"
set "COOKIE=%TEMP%\iot-ids-real-cookies.txt"
set "IFACE=wlan0"

set /p "PASS=Enter admin password: "
set /p "IFACEOK=Capture interface [wlan0]: " || set "IFACEOK=wlan0"
if not "%IFACEOK%"=="" set "IFACE=%IFACEOK%"

echo [1/4] Login admin...
curl -s -c "%COOKIE%" -X POST "%BASE%/api/auth/login" -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"%PASS%\"}"
echo.

echo [2/4] Start real capture (scapy, interface=%IFACE%)...
curl -s -b "%COOKIE%" -X POST "%BASE%/api/capture/start" -H "Content-Type: application/json" -d "{\"use_scapy\":true,\"interface\":\"%IFACE%\"}"
echo.

echo [3/4] Wait 60 seconds for traffic (press any key to skip)...
timeout /t 60

echo [4/4] Trigger GNN detection...
curl -s -b "%COOKIE%" -X POST "%BASE%/api/device/detect"
echo.

echo Done. level: 3=red(botnet) 2=orange 1=yellow 0=green.
endlocal

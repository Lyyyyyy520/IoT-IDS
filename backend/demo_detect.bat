@echo off
setlocal EnableExtensions
set "BASE=http://127.0.0.1:5000"
set "COOKIE=%TEMP%\iot-ids-cookies.txt"

set /p "PASS=Enter admin password: "
echo.

echo [1/4] Login admin...
curl -s -c "%COOKIE%" -X POST "%BASE%/api/auth/login" -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"%PASS%\"}"
echo.

echo [2/4] Start capture simulation (25%% attack)...
curl -s -b "%COOKIE%" -X POST "%BASE%/api/capture/start" -H "Content-Type: application/json" -d "{\"use_scapy\":false,\"attack_ratio\":0.25}"
echo.

echo [3/4] Wait 180 seconds for traffic (press any key to skip)...
timeout /t 180

echo [4/4] Trigger GNN detection...
curl -s -b "%COOKIE%" -X POST "%BASE%/api/device/detect"
echo.

echo Done. level: 3=red(botnet) 2=orange 1=yellow 0=green.
endlocal

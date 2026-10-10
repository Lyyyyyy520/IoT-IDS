@echo off
rem Dev-only backend launcher for CMD. Enables local MQTT ingestion (127.0.0.1:1884).
rem For the field gateway, change IOT_IDS_MQTT_HOST/PORT below to 192.168.4.1 / 1883.
cd /d "%~dp0"

rem --- Interpreter (change to your local python if different) ---
set "PYTHON=D:\python\python.exe"

rem --- Required runtime settings ---
set "IOT_IDS_ENV=development"
set "IOT_IDS_DATABASE_PATH=D:\Project\iot-ids\backend\data\ids.db"
set "IOT_IDS_MOBILE_ALLOW_INSECURE_HTTP=true"
set "IOT_IDS_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000"

rem --- MQTT ingestion (local sim: 127.0.0.1:1884  /  field gateway: 192.168.4.1:1883) ---
set "IOT_IDS_MQTT_ENABLED=true"
set "IOT_IDS_MQTT_HOST=127.0.0.1"
set "IOT_IDS_MQTT_PORT=1884"
set "IOT_IDS_MQTT_TLS_ENABLED=false"
set "IOT_IDS_MQTT_BACKEND_USERNAME=iot-ids-backend"
set "IOT_IDS_MQTT_BACKEND_PASSWORD=dev-only-plaintext"
set "IOT_IDS_MQTT_CLIENT_ID=iot-ids-heartbeat-subscriber"

rem --- Optional stable secrets (uncomment + fill to keep sessions across restarts).
rem     Generate with:  python -c "import secrets; print(secrets.token_hex(32))"
rem set "IOT_IDS_SESSION_SECRET=<64 hex chars>"
rem set "IOT_IDS_MOBILE_TOKEN_SECRET=<64 hex chars>"
rem set "IOT_IDS_PROBE_TOKEN=<64 hex chars>"

"%PYTHON%" app.py

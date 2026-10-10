#!/usr/bin/env bash
# Dev-only backend launcher: enables local MQTT ingestion from 127.0.0.1:1884.
# Mirrors the running dev interpreter (D:\python\python.exe). Secrets persist in
# .dev-runtime-secrets (gitignored) so sessions survive restarts.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="D:/python/python.exe"
SECRETS_FILE=".dev-runtime-secrets"

if [[ ! -f "$SECRETS_FILE" ]]; then
  "$PYTHON" - "$SECRETS_FILE" <<'PY'
import secrets, sys
with open(sys.argv[1], "w", encoding="utf-8") as f:
    for name in ("IOT_IDS_SESSION_SECRET", "IOT_IDS_MOBILE_TOKEN_SECRET", "IOT_IDS_PROBE_TOKEN"):
        f.write(f"{name}={secrets.token_hex(32)}\n")
    f.write(f"IOT_IDS_MQTT_BACKEND_PASSWORD={secrets.token_hex(16)}\n")
PY
  echo "[run_dev_backend] generated $SECRETS_FILE"
fi

set -a
source "$SECRETS_FILE"
set +a

export IOT_IDS_ENV=development
export IOT_IDS_DATABASE_PATH="D:/Project/iot-ids/backend/data/ids.db"
export IOT_IDS_MOBILE_ALLOW_INSECURE_HTTP=true
export IOT_IDS_CORS_ORIGINS="http://localhost:3000,http://127.0.0.1:3000"
export IOT_IDS_MQTT_ENABLED=true
export IOT_IDS_MQTT_HOST=127.0.0.1
export IOT_IDS_MQTT_PORT=1884
export IOT_IDS_MQTT_TLS_ENABLED=false
export IOT_IDS_MQTT_BACKEND_USERNAME=iot-ids-backend
export IOT_IDS_MQTT_CLIENT_ID=iot-ids-heartbeat-subscriber

exec "$PYTHON" app.py

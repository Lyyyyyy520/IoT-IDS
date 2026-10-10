#!/usr/bin/env bash
# 树莓派网关一键搭建（6 台物理 ESP32 现场演示版）
#
# 对应 docs/16-field-test-checklist.md 阶段 B（网关）+ D（后端集成）。
# 纯固件场景：hotspot iot-community + dnsmasq（避开 .10~.15 静态段）+
# mosquitto（6 设备账号 + pattern ACL）+ 可选后端环境（--with-backend）。
#
# 用法（在 64 位 Raspberry Pi OS 上，root 运行）：
#   sudo bash edge/setup_pi_gateway.sh [--with-backend] [REPO_DIR]
#   REPO_DIR 默认 /home/pi/iot-ids（仓库需已拷贝到 Pi）
#
# !! 无头（SSH）执行注意：最后一步会把 wlan0 切成热点，SSH 会断——
#    请在 tmux 里运行：sudo tmux new -s setup
#    热点起来后笔记本连 iot-community，再 ssh pi@192.168.4.1 并 tmux attach -t setup
set -euo pipefail

WIFI_IFACE="${WIFI_IFACE:-wlan0}"
SSID="iot-community"
WIFI_PASS="12345678"
BACKEND_USER="iot-ids-backend"
DEVICES=(camera-01 door-01 light-01 plug-01 sensor-01 speaker-01)
PASSWD_FILE="/etc/mosquitto/passwd-iot-ids"
PASSWD_MAP="/root/iot-ids-device-passwords.txt"
REPO_DIR="/home/pi/iot-ids"
WITH_BACKEND=0

for arg in "$@"; do
    case "$arg" in
        --with-backend) WITH_BACKEND=1 ;;
        *) REPO_DIR="$arg" ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 root 运行：sudo bash $0 $*" >&2
    exit 1
fi

echo "== 0. 前置检查 =="
ARCH="$(uname -m)"
echo "架构: $ARCH"
if [ "$ARCH" != "aarch64" ]; then
    echo "!! 警告：onnxruntime 只有 aarch64 wheel，32 位系统上后端无法运行（只搭网关可忽略）。"
fi
if [ ! -d "$REPO_DIR" ]; then
    echo "!! 仓库目录不存在：$REPO_DIR（后端相关步骤会跳过，可只搭网关）"
fi

echo "== 1. 安装软件包（需要联网；先做联网步骤，网络切换放最后）=="
apt-get update -y
apt-get install -y hostapd dnsmasq mosquitto mosquitto-clients tcpdump openssl python3-venv python3-pip

if [ "$WITH_BACKEND" -eq 1 ]; then
    echo "== 2. 后端依赖（venv，需要联网，在网络切换前完成）=="
    [ -d "$REPO_DIR" ] || { echo "!! 仓库不存在，跳过"; exit 1; }
    # Bookworm 的 PEP 668 禁止系统级 pip install —— 一律走项目内 venv
    VENV_PY="$REPO_DIR/backend/.venv-pi/bin/python"
    if [ ! -x "$VENV_PY" ]; then
        python3 -m venv "$REPO_DIR/backend/.venv-pi"
    fi
    "$VENV_PY" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple || \
        "$VENV_PY" -m pip install --upgrade pip
    "$VENV_PY" -m pip install -r "$REPO_DIR/backend/requirements.txt" \
        -i https://pypi.tuna.tsinghua.edu.cn/simple || \
        "$VENV_PY" -m pip install -r "$REPO_DIR/backend/requirements.txt"
    # 模型三件套必须就位（onnx + 外部权重 + 归一化参数）
    for f in device_gnn_hetero.onnx device_gnn_hetero.onnx.data device_gnn_norm.npz; do
        if [ ! -f "$REPO_DIR/backend/data/$f" ]; then
            echo "!! 缺少模型文件 backend/data/$f —— 从开发机拷贝后再启动后端"
        fi
    done

    echo "== 3. 初始化全新演示数据库 =="
    DB_PATH="${IOT_IDS_DATABASE_PATH:-$REPO_DIR/backend/data/ids-pi.db}"
    BACKUP_DIR="$REPO_DIR/backend/data/backups"
    mkdir -p "$BACKUP_DIR"
    if [ ! -f "$DB_PATH" ]; then
        (cd "$REPO_DIR/backend" && IOT_IDS_BOOTSTRAP_ADMIN_PASSWORD= "$VENV_PY" - "$DB_PATH" <<'PY'
import sys
sys.path.insert(0, '.')
from database import init_db
from v3_database import initialize_v3_database
path = sys.argv[1]
init_db(path)                 # 旧库表（users/alerts/...；admin 引导校验要求存在）
initialize_v3_database(path)  # v3 表
print("database_initialized")
PY
)
    fi
    "$VENV_PY" "$REPO_DIR/backend/v3_db_upgrade.py" apply --database "$DB_PATH" --backup-directory "$BACKUP_DIR"
    # 幂等：已有管理员则跳过引导（重跑脚本时不中断）
    ADMIN_EXISTS="$("$VENV_PY" -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute(\"SELECT COUNT(*) FROM users WHERE role='admin'\").fetchone()[0])" "$DB_PATH")"
    if [ "$ADMIN_EXISTS" = "0" ]; then
        echo ">> 创建管理员（交互式输入密码，只输一次，登录前端用）："
        "$VENV_PY" "$REPO_DIR/backend/v3_admin_bootstrap.py" --database "$DB_PATH" --username admin
    else
        echo ">> 管理员已存在，跳过引导"
    fi
fi

echo "== 4. mosquitto（1883 + pattern ACL；纯本地配置，不影响 SSH）=="
# 只写必要项：新版 mosquitto 拒绝与系统默认配置重复的 persistence/log 项
cat > /etc/mosquitto/conf.d/iot-ids.conf <<EOF
listener 1883 192.168.4.1
allow_anonymous false
password_file $PASSWD_FILE
acl_file /etc/mosquitto/acl-iot-ids
EOF
# 清掉旧版搭建留下的匿名监听（若存在），避免 1883 冲突
if [ -f /etc/mosquitto/conf.d/iot.conf ]; then
    mv /etc/mosquitto/conf.d/iot.conf "/root/iot.conf.old-$(date +%s).bak" 2>/dev/null || true
fi
cp "$(dirname "$0")/mosquitto/acl.example" /etc/mosquitto/acl-iot-ids

PASSWORDS_ENV="$REPO_DIR/edge/pi-passwords.env"
if [ -f "$PASSWORDS_ENV" ]; then
    # 从仓库密码对照表确定性重建（幂等，避免旧哈希残留导致 "not authorised"）。
    # tr -d '\r' 去 CR：Windows 侧文件可能带 CRLF，不带会存进"带回车符的密码"哈希，
    # 与板载干净密码永远对不上（2026-10-09 实测坑）。
    : > "$PASSWD_FILE"
    BACKEND_PASS="$(grep '^backend_mqtt_password=' "$PASSWORDS_ENV" | cut -d= -f2 | tr -d '\r')"
    mosquitto_passwd -b "$PASSWD_FILE" "$BACKEND_USER" "$BACKEND_PASS"
    for dev in "${DEVICES[@]}"; do
        pass="$(grep "^${dev}=" "$PASSWORDS_ENV" | cut -d= -f2 | tr -d '\r')"
        mosquitto_passwd -b "$PASSWD_FILE" "$dev" "$pass"
    done
    cp "$PASSWORDS_ENV" "$PASSWD_MAP"
    echo "已按仓库密码对照表重建密码文件"
else
    if [ ! -s "$PASSWD_FILE" ]; then
        touch "$PASSWD_FILE"
        BACKEND_PASS="$(openssl rand -hex 16)"
        mosquitto_passwd -b "$PASSWD_FILE" "$BACKEND_USER" "$BACKEND_PASS"
        : > "$PASSWD_MAP"
        echo "# 生成时间：$(date)  勿提交到仓库" > "$PASSWD_MAP"
        echo "backend_mqtt_user=$BACKEND_USER" >> "$PASSWD_MAP"
        echo "backend_mqtt_password=$BACKEND_PASS" >> "$PASSWD_MAP"
        for dev in "${DEVICES[@]}"; do
            pass="$(openssl rand -hex 8)"
            mosquitto_passwd -b "$PASSWD_FILE" "$dev" "$pass"
            echo "${dev}=${pass}" >> "$PASSWD_MAP"
        done
        echo "已随机生成设备 MQTT 密码对照表：$PASSWD_MAP（烧录前需同步进 device_secrets.h）"
    else
        echo "密码文件已存在，跳过生成（对照表见 $PASSWD_MAP）"
    fi
fi
# mosquitto 2.x：passwd 文件需 root 所有 + 0700（启动时 root 打开后降权）
chown root:root "$PASSWD_FILE"
chmod 0700 "$PASSWD_FILE"
chmod 600 "$PASSWD_MAP" 2>/dev/null || true
systemctl enable mosquitto
# 注意：不在这里 restart mosquitto —— listener 绑定 192.168.4.1，
# 该地址要到网络切换后才存在；启动放到最后。

if [ "$WITH_BACKEND" -eq 1 ]; then
    echo "== 5. 生成后端启动脚本（env 已按现场配好）=="
    DB_PATH="${IOT_IDS_DATABASE_PATH:-$REPO_DIR/backend/data/ids-pi.db}"
    BACKEND_MQTT_PASS="$(grep '^backend_mqtt_password=' "$PASSWD_MAP" | cut -d= -f2 | tr -d '\r')"
    cat > "$REPO_DIR/backend/run_backend_pi.sh" <<EOF
#!/usr/bin/env bash
# 现场演示后端启动器（Pi 本机，root 运行：scapy 抓 wlan0 需要 CAP_NET_RAW）
set -euo pipefail
cd "\$(dirname "\$0")"
export IOT_IDS_ENV=development
export IOT_IDS_DATABASE_PATH="$DB_PATH"
export IOT_IDS_MOBILE_ALLOW_INSECURE_HTTP=true
export IOT_IDS_CORS_ORIGINS="http://192.168.4.9:3000,http://localhost:3000"
export IOT_IDS_MQTT_ENABLED=true
export IOT_IDS_MQTT_HOST=192.168.4.1
export IOT_IDS_MQTT_PORT=1883
export IOT_IDS_MQTT_TLS_ENABLED=false
export IOT_IDS_MQTT_BACKEND_USERNAME="$BACKEND_USER"
export IOT_IDS_MQTT_BACKEND_PASSWORD="$BACKEND_MQTT_PASS"
export IOT_IDS_MQTT_CLIENT_ID=iot-ids-heartbeat-subscriber
export IOT_IDS_TOPO_SCENARIO=physical
export IOT_IDS_SESSION_SECRET="\$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
export IOT_IDS_MOBILE_TOKEN_SECRET="\$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
export IOT_IDS_PROBE_TOKEN="\$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
# 注意：SESSION_SECRET 每次重启会换新 —— 演示日要固定的话，把上面三行换成固定值
exec ./.venv-pi/bin/python app.py
EOF
    chmod +x "$REPO_DIR/backend/run_backend_pi.sh"
fi

if systemctl is-active --quiet NetworkManager; then
    echo "== 6. 热点：NetworkManager 方案（系统自管 AP + DHCP，含 .10~.15 保留）=="
    # 热点连接 iot-hotspot：幂等创建/修正（SSID/密码/共享 IP）
    if nmcli -t -f NAME con show 2>/dev/null | grep -qx 'iot-hotspot'; then
        nmcli con mod iot-hotspot \
            802-11-wireless.ssid "$SSID" \
            802-11-wireless-security.key-mgmt wpa-psk \
            802-11-wireless-security.psk "$WIFI_PASS" \
            ipv4.method shared ipv4.addresses 192.168.4.1/24
    else
        nmcli con add type wifi con-name iot-hotspot ifname "$WIFI_IFACE" \
            ssid "$SSID" 802-11-wireless.mode ap \
            802-11-wireless-security.key-mgmt wpa-psk \
            802-11-wireless-security.psk "$WIFI_PASS" \
            ipv4.method shared ipv4.addresses 192.168.4.1/24
    fi
    nmcli con mod iot-hotspot connection.autoconnect yes
    # DHCP 保留 .10~.15（占位 MAC 仅作保留），防止发给手机/笔记本撞设备静态 IP
    mkdir -p /etc/NetworkManager/dnsmasq-shared.d
    cat > /etc/NetworkManager/dnsmasq-shared.d/reserve-devices.conf <<EOF
dhcp-host=00:00:00:00:00:10,192.168.4.10
dhcp-host=00:00:00:00:00:11,192.168.4.11
dhcp-host=00:00:00:00:00:12,192.168.4.12
dhcp-host=00:00:00:00:00:13,192.168.4.13
dhcp-host=00:00:00:00:00:14,192.168.4.14
dhcp-host=00:00:00:00:00:15,192.168.4.15
EOF
    # 系统 dnsmasq 与 NM 内置 dnsmasq 冲突；AP 由 NM 内部后端承担
    systemctl disable --now dnsmasq 2>/dev/null || true
    systemctl disable --now hostapd 2>/dev/null || true

    echo "== 7. 网络切换（NM 激活热点；!! SSH 会断）=="
    nmcli con down iot-hotspot 2>/dev/null || true
    sleep 2
    nmcli con up iot-hotspot
else
    echo "== 6. hostapd（SSID=$SSID；注意不要开 ap_isolate：板间 CoAP/洪水依赖客户端互通）=="
    cat > /etc/hostapd/hostapd.conf <<EOF
interface=$WIFI_IFACE
driver=nl80211
ssid=$SSID
hw_mode=g
channel=6
wpa=2
wpa_passphrase=$WIFI_PASS
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF
    sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd

    echo "== 7. dnsmasq（只发 .2~.9 与 .16~.99，避开 .10~.15 设备静态段）=="
    cat > /etc/dnsmasq.conf <<EOF
interface=$WIFI_IFACE
bind-interfaces
dhcp-authoritative
dhcp-range=192.168.4.2,192.168.4.9,255.255.255.0,24h
dhcp-range=192.168.4.16,192.168.4.99,255.255.255.0,24h
EOF

    echo "== 8. 静态 IP 192.168.4.1 + 网络切换（!! 最后一步，SSH 会断）=="
    if ! grep -q "static ip_address=192.168.4.1" /etc/dhcpcd.conf 2>/dev/null; then
        cat >> /etc/dhcpcd.conf <<EOF

interface $WIFI_IFACE
static ip_address=192.168.4.1/24
nohook wpa_supplicant
EOF
    fi
    systemctl restart dhcpcd 2>/dev/null || true
    systemctl unmask hostapd 2>/dev/null || true
    systemctl enable hostapd dnsmasq
    systemctl restart dnsmasq
    systemctl restart hostapd || echo "!! hostapd 启动失败（常见：wlan0 被占用或驱动不支持 AP），现场排查后再启"
fi

# mosquitto 现在才能绑定 192.168.4.1（网络已切换）；并保证开机时等网络就绪
mkdir -p /etc/systemd/system/mosquitto.service.d
cat > /etc/systemd/system/mosquitto.service.d/wait-network.conf <<EOF
[Unit]
After=network-online.target
Wants=network-online.target
EOF
systemctl daemon-reload
systemctl restart mosquitto || echo "!! mosquitto 启动失败：systemctl status mosquitto 查看"

echo
echo "== 完成 =="
echo "  WiFi 已切为热点 $SSID（密码 $WIFI_PASS）。SSH 若断：笔记本连热点后"
echo "  ssh pi@192.168.4.1 并 tmux attach -t setup 回到本会话。"
echo "  网关验收："
echo "  1. 手机能搜到 $SSID 并连上，拿到 192.168.4.x"
echo "  2. mosquitto_sub -h 192.168.4.1 -t 'community/+/status' -u $BACKEND_USER -P <密码>"
echo "  3. 设备密码对照表 $PASSWD_MAP"
if [ "$WITH_BACKEND" -eq 1 ]; then
    echo "  后端验收：sudo tmux new -s ids; sudo bash $REPO_DIR/backend/run_backend_pi.sh"
    echo "    curl http://192.168.4.1:5000/api/health 返回 ok"
    echo "  攻击演示：mosquitto_pub -h 192.168.4.1 -u $BACKEND_USER -P <密码> \\"
    echo "    -t community/door-01/control -m attack（板子需已开 IOT_LAB_ATTACK_ENABLED）"
fi

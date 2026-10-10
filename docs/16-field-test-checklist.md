# 16 - 完整硬件 + 虚拟设备通路现场测试清单

> 状态：操作手册（当前 v3 架构）
> 更新日期：2026-10-08
> 前置：阶段 A（烧录 6 块 ESP32：5× C3 + 1× ESP32-CAM）已完成。
> 关联：[12-community-simulation.md](12-community-simulation.md)、`edge/esp32/README.md`、`edge/mosquitto/`、`backend/config.py`。

本清单把「阶段 B 树莓派网关 → C 虚拟设备 → D 后端集成 → E 端到端联调」整理成可照着做的步骤，
每步带验收判据。目标是跑通：**6 台物理 ESP32 + 30 台虚拟设备 → 树莓派网关 → 后端 → 设备图/事件**。

---

## 0. 先确认三个前提（否则后面会卡住）

1. **热点密码与固件一致**：ESP32 固件里 `device_secrets.h` 的 `IOT_WIFI_PASSWORD` 必须和树莓派热点密码一致
   （计划用 `iot-community` / `12345678`）。密码不一致 → 设备连不上热点。
2. **物理设备走「发现→认领」**：6 台物理设备（camera-01 … speaker-01）不在 `v3_device_profiles` 里预登记，
   上电后靠 MAC 发现、再认领；30 台虚拟设备已由 `v3_device_seed.py` 预登记（幂等）。所以图上最终是 **36 个设备节点**。
3. **虚拟设备的 MQTT 认证**：`virtual_device_generator.py` 目前是**匿名连接**
   （`client.connect(BROKER, BROKER_PORT)`，无用户名密码）。而 `mosquitto.conf.example` 是 `allow_anonymous false`。
   → 二选一（见阶段 B 第 3 步）：
   - **实验室简化**：Pi broker 用 `allow_anonymous true`（隔离网、不连公网，可接受）；
   - **安全正确**：给 30 台虚拟设备在 Pi 的 passwd 里建账号（或一个共享 `iot-ids-device` 账号）并补 ACL。

---

## 阶段 B：树莓派网关（192.168.4.1）

### B1. 热点 hostapd（SSID=iot-community）

```bash
sudo apt update && sudo apt install -y hostapd dnsmasq
sudo tee /etc/hostapd/hostapd.conf > /dev/null <<'EOF'
interface=wlan0
driver=nl80211
ssid=iot-community
hw_mode=g
channel=6
wpa=2
wpa_passphrase=12345678
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF
```

### B2. DHCP（dnsmasq）+ Pi 静态 IP

- DHCP 范围避开 `.10~.15`（物理静态）和 `.100~.129`（虚拟多 IP），只发 `.2~.99`：

```bash
sudo tee /etc/dnsmasq.conf > /dev/null <<'EOF'
interface=wlan0
dhcp-range=192.168.4.2,192.168.4.99,255.255.255.0,24h
EOF
echo -e "\ninterface wlan0\nstatic ip_address=192.168.4.1/24\nnohook wpa_supplicant" | sudo tee -a /etc/dhcpcd.conf
```

### B3. Mosquitto broker（1883，带密码 + ACL）

按 `edge/mosquitto/mosquitto.conf.example` 部署到 `/etc/mosquitto/conf.d/iot-ids.conf`：

```bash
# listener 1883 192.168.4.1 / allow_anonymous false / password_file / acl_file
sudo mosquitto_passwd -c /etc/mosquitto/passwd-iot-ids iot-ids-backend   # 后端订阅账号
# 每台物理设备一个账号（username = device_id，密码唯一）
sudo mosquitto_passwd /etc/mosquitto/passwd-iot-ids door-01
# … camera-01 / light-01 / plug-01 / sensor-01 / speaker-01 同理
```

ACL 按 `edge/mosquitto/acl-iot-ids.example` 生成：
- 后端账号：`topic read community/+/status`（只读订阅）。
- 每台设备账号：`topic write community/{id}/status` + `topic read community/{id}/control`。

> 若选「实验室简化」：把 `allow_anonymous false` 改成 `true` 并去掉 passwd/acl，30 台虚拟设备即可匿名接入；
> 但此时不要接公网。**推荐**：隔离网 + `allow_anonymous true`（最省事），正式演示再切回账号模式。

### B4. 验收

- [ ] 手机能搜到 `iot-community` 并连上，拿到 192.168.4.x。
- [ ] `mosquitto_sub -h 192.168.4.1 -p 1883 -t 'community/+/status'` 能订阅（账号模式下需 `-u/-P`）。

---

## 阶段 C：虚拟设备（Windows 电脑或 Ubuntu VM）

### C1. 改 broker 指向

`edge/community_devices.py`：

```python
BROKER = '192.168.4.1'
BROKER_PORT = 1883
```

（本地模拟时才是 `127.0.0.1` / `1884`。）

### C2. 绑定 30 个虚拟 IP（.100~.129）

- Windows（管理员）：`edge/setup_virtual_ips.bat`（把 `IFACE` 改成你的 WiFi 网卡名，如 `WLAN`）。
- Linux/VM：`sudo bash edge/setup_virtual_ips.sh`（注意当前脚本只挂 `.100~.120`，需补到 `.129`）。

### C3. 电脑连上热点 + 跑生成器

```bash
pip install paho-mqtt
python edge/virtual_device_generator.py            # 全正常
# 触发某台被感染：python edge/virtual_device_generator.py --attack plug-03
```

> 若走账号模式，生成器需先补 username/password 参数（当前是匿名）；简化模式则无需改。

### C4. 验收

- [ ] 在 Pi 上 `mosquitto_sub -h 192.168.4.1 -t 'community/+/status'` 能看到 30 台设备的心跳（`ip` 为 192.168.4.100~.129）。
- [ ] 后端摄入后这 30 台在 DB 里变 `online`（阶段 D 完成后一起看）。

---

## 阶段 D：后端集成（连 Pi 的 broker）

后端跑在**连了 Pi 热点的电脑**上（或 Pi 本身）。改 `backend/run_dev_backend.sh`（或手写 env）：

```bash
export IOT_IDS_MQTT_ENABLED=true
export IOT_IDS_MQTT_HOST=192.168.4.1
export IOT_IDS_MQTT_PORT=1883
export IOT_IDS_MQTT_TLS_ENABLED=false
export IOT_IDS_MQTT_BACKEND_USERNAME=iot-ids-backend
export IOT_IDS_MQTT_BACKEND_PASSWORD='<B3 里给后端的密码>'
export IOT_IDS_MQTT_CLIENT_ID=iot-ids-heartbeat-subscriber
# 其余（DB 路径 / 会话密钥 / CORS / 移动端 HTTP）保持不变
python app.py
```

### D 验收

- [ ] `/api/health` 返回 ok；日志无 `mqtt_subscriber_*_failed`。
- [ ] 后端进程有到 `192.168.4.1:1883` 的 ESTABLISHED 连接。
- [ ] `v3_device_current_state` 里 30 台虚拟设备 `online`。

---

## 阶段 E：端到端联调（检测闭环）

### E1. 全设备在线

- 6 台物理 ESP32 上电 → 连 `iot-community` → 发心跳 → 后端「发现」到 6 台 → 认领后与 30 台虚拟设备共 **36 台 online**。

### E2. 设备图

- Monitor「设备关系主视图」应显示 **36 个节点**，按风险/在线着色。
- 边来自 `v3_device_traffic_peer_minutes`：物理设备间 CoAP 联动 + 虚拟设备 CoAP 联动 + 攻击时的内部扫描边。

### E3. 流量边

- 登录 admin → `POST /api/capture/start`（`use_scapy=false` 走仿真，或 `true` + `interface=wlan0` 走真实抓包）。
- 跑几分钟后 `v3_device_traffic_peer_minutes` 出现设备间边（`relation_type=observed_traffic`）。

### E4. 攻击模式（隔离环境，可选）

```bash
mosquitto_pub -h 192.168.4.1 -t "community/plug-03/control" -m "attack"
# 等检测窗口 → /api/device/detect 应把 plug-03 判「僵尸网络(红)」
mosquitto_pub -h 192.168.4.1 -t "community/plug-03/control" -m "normal"
```

> 攻击实验默认关闭；启用前确认目标在 `192.168.4.0/24` 内、Pi 禁止转发公网、ACL 已生效、有断电手段。

### E5. 完成判据

1. ✅ 6 物理 + 30 虚拟 = 36 台设备连上热点、发 MQTT 遥测。
2. ✅ 设备图 36 节点，风险/在线着色正确。
3. ✅ 流量边（CoAP 联动 + 攻击扫描边）在图上出现。
4. ✅ 触发攻击 → 被感染设备判红、生成 v3 incident；恢复后正常。

---

## 常见坑速查

| 症状 | 原因 | 处理 |
|------|------|------|
| 设备连不上热点 | 热点密码 ≠ 固件 `IOT_WIFI_PASSWORD` | 对齐密码 |
| 后端收不到心跳 | MQTT host/port 错，或账号/ACL 拒 | 检查 D 的 env 与 B3 的 passwd/acl |
| 虚拟设备连不上 broker | 匿名 vs `allow_anonymous false` | 用简化模式或补账号 |
| 抓不到设备间流量 | 设备没连 Pi 热点（连了别的 WiFi） | 确认连 `iot-community` |
| 设备图只有 30 节点 | 6 台物理未「发现→认领」 | 走设备发现/认领流程 |

---

## 2026-10-09 补遗：6 台物理设备纯固件演示（虚拟设备已清库）

现状：30 台虚拟设备已从数据库清空（备份见 `backend/data/backups/`），
当前基线为 6 台物理 ESP32；GNN 已做部署域适配重训（见
`dev-logs/2026-10-08.md` 重训章节与 `backend/probe_gnn_six_devices.py`）。

补三步（本清单此前缺失）：

1. **DHCP 与静态段**：固件用 `WiFi.config(STATIC_IP)` 静态 .10~.15，不会漂移；
   真正风险是 DHCP 客户端（笔记本/手机）被分到 .10~.15 造成 ARP 冲突。
   dnsmasq 范围必须避开：`.2~.9` 与 `.16~.99` 两段（`edge/setup_pi_gateway.sh` 已内置）。
   建议笔记本静态配 .9 并写进后端 CORS 白名单。

2. **受害机 192.168.4.200（攻击演示必须项）**：第二台笔记本/手机静态配 .200
   （dnsmasq 不发放该地址，无冲突），跑一个 UDP 9000 计数监听器展示被洪水命中。
   无 .200 主机时 lwIP ARP 失败、洪水不出天线、攻击段变哑剧。

3. **移动端现场配置（可选加分）**：手机连 `iot-community`，Expo 指向
   `http://192.168.4.1:5000`，后端 `IOT_IDS_MOBILE_ALLOW_INSECURE_HTTP=true`
   （Pi 启动脚本已设）。

一键搭建：`sudo bash edge/setup_pi_gateway.sh [--with-backend]`（生成 6 设备
MQTT 密码对照表，烧录前写进各板 `device_secrets.h`）。

演示攻击形态（重训后经验）：**UDP 洪水（内网打受害机 .200 + 外网洪水），
不做内网 SYN 扫描**——扫描边会在 GNN 图注意力里把怀疑信号传染给被扫设备；
纯洪水形态攻击者稳定判红、其余全绿（验证脚本场景 5/6）。

后端 MQTT 切 Pi：`backend/run_backend_pi.sh`（setup 脚本自动生成），
拓扑场景 `IOT_IDS_TOPO_SCENARIO=physical` 显示 6 节点 + 6 条 configured 联动边。

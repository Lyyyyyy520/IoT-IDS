# 09 - 现场部署步骤清单（硬件到货后照着做）

> 状态：部署操作手册
> 更新日期：2026-08-28
> 前置：已购置 ESP32-C3 经典款 ×5 + ESP32-CAM ×1（¥120）

本文档是硬件到货后的完整部署 checklist，按顺序执行即可跑通 P0 检测闭环。

---

## 第 0 步：硬件清单确认

- [ ] 树莓派 4B（已有）
- [ ] ESP32-C3 经典款 ×5
- [ ] ESP32-CAM ×1
- [ ] USB 数据线（给 ESP32 烧录）
- [ ] 杜邦线若干（接执行器，P1 才用）

---

## 第 1 步：树莓派开 WiFi 热点（192.168.4.x）

目标：让所有 ESP32 连 Pi 的热点，Pi 成为网关（否则抓不到设备间流量）。

```bash
# 1.1 安装热点 + DHCP 软件
sudo apt update && sudo apt install -y hostapd dnsmasq

# 1.2 配置热点（SSID=iot-community, 密码=12345678）
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

# 1.3 配置 DHCP（分配 192.168.4.x）
sudo tee /etc/dnsmasq.conf > /dev/null <<'EOF'
interface=wlan0
dhcp-range=192.168.4.2,192.168.4.100,255.255.255.0,24h
EOF

# 1.4 固定 Pi 自己的 IP 为 192.168.4.1（编辑 /etc/dhcpcd.conf 追加）
echo -e "\ninterface wlan0\nstatic ip_address=192.168.4.1/24\nnohook wpa_supplicant" | sudo tee -a /etc/dhcpcd.conf

# 1.5 启动
sudo systemctl unmask hostapd
sudo systemctl enable hostapd dnsmasq
sudo reboot
```

- [ ] 重启后用手机搜到 `iot-community` 热点 → 热点成功

## 第 2 步：树莓派装 MQTT Broker + Python 依赖

```bash
# 2.1 MQTT Broker
sudo apt install -y mosquitto mosquitto-clients

# 2.2 Python 依赖（注意用项目 venv 或系统 python）
pip install flask onnxruntime scapy numpy pandas

# 2.3 把项目后端代码拷到树莓派（git clone 或 scp）
git clone https://github.com/Lyyyyyy520/IoT-IDS.git
cd IoT-IDS/backend
```

- [ ] `mosquitto -h` 能运行 → MQTT 装好

## 第 3 步：ESP32 固件烧录（6 台设备）

在**电脑上**（不是树莓派）用 Arduino IDE 烧录：

```bash
# 3.1 装 Arduino IDE（arduino.cc 下载）

# 3.2 装 ESP32 支持包
#  文件 → 首选项 → 附加开发板管理器网址加:
#  https://espressif.github.io/arduino-esp32/package_esp32_index.json
#  工具 → 开发板 → 开发板管理器 → 搜索安装 "esp32"

# 3.3 装依赖库（库管理器搜索）
#  - PubSubClient
#  - DHT sensor library（仅传感器）
#  - ESP32Servo（仅门禁、摄像头）
```

**每台设备烧录前改 3 处**（`edge/esp32/community_device/community_device.ino`）：

| 设备 | DEVICE_TYPE | DEVICE_ID | 固件 |
|------|-------------|-----------|------|
| 门禁 | DEVICE_DOOR | door-01 | community_device |
| 路灯 | DEVICE_LIGHT | light-01 | community_device |
| 插座 | DEVICE_PLUG | plug-01 | community_device |
| 传感器 | DEVICE_SENSOR | sensor-01 | community_device |
| 音箱 | DEVICE_SPEAKER | speaker-01 | community_device |
| 摄像头 | — | camera-01 | camera_device |

- [ ] 6 台设备都烧录成功（串口监视器看到"设备启动"）

## 第 4 步：部署后端 + 启动

```bash
# 4.1 确认模型文件在 backend/data/
ls backend/data/device_gnn.onnx backend/data/device_gnn_norm.npz

# 4.2 启动 Flask 后端
cd backend
python app.py
```

- [ ] 后端启动，访问 `http://192.168.4.1:5000/api/health` 返回 ok

## 第 5 步：联调 P0（检测闭环）

```bash
# 5.1 设备上电 → 自动连 iot-community 热点 → 发 MQTT 遥测

# 5.2 验证设备已连上（在 Pi 上订阅遥测）
mosquitto_sub -h 192.168.4.1 -t "community/+/status"

# 5.3 启动抓包（通过 API）
curl -X POST http://192.168.4.1:5000/api/capture/start \
  -H "Content-Type: application/json" \
  -d '{"use_scapy": true}'

# 5.4 触发设备检测（每 60 秒一次，或手动触发）
curl -X POST http://192.168.4.1:5000/api/device/detect \
  -H "Authorization: Bearer <token>"

# 5.5 查看设备风险等级
curl http://192.168.4.1:5000/api/device/status \
  -H "Authorization: Bearer <token>"
```

- [ ] 遥测订阅能看到设备状态 → 设备通信正常
- [ ] `/api/device/detect` 返回设备风险等级 → 检测闭环通

## 第 6 步：触发攻击模式测试

```bash
# 6.1 让门禁"被感染"（发 Mirai UDP 洪水 + 舵机开门）
mosquitto_pub -h 192.168.4.1 -t "community/door-01/control" -m "attack"

# 6.2 等几秒后检测
curl -X POST http://192.168.4.1:5000/api/device/detect \
  -H "Authorization: Bearer <token>"

# 6.3 隔离门禁
mosquitto_pub -h 192.168.4.1 -t "community/door-01/control" -m "block"
```

- [ ] 攻击后 `/api/device/detect` 把门禁判为"僵尸网络(红)" → 检测成功

## 第 7 步：域偏移校准（关键！）

**预期现象**：第一次联调时，设备可能被误判（如正常设备判成"拒绝服务"）。

这是域偏移（现场 ESP32 流量 ≠ CICIoT2023 训练分布），校准方法：

```bash
# 7.1 观察设备的实际流量特征（抓包看）
# 7.2 调整固件遥测参数，使流量接近训练分布：
#     - community_device.ino 里的 TELEMETRY_MS（遥测间隔）
#     - 遥测的包大小、协议（TCP/UDP）
#     - 攻击模式的发包速率

# 7.3 反复测：正常设备应判"绿"，攻击设备应判"红"
```

- [ ] 正常设备稳定判"绿"、攻击设备稳定判"红" → 校准完成

---

## 常见问题排查

| 问题 | 原因 | 解决 |
|------|------|------|
| 设备连不上热点 | SSID/密码不对，或热点没开 | 检查 hostapd 配置，重启 hostapd |
| MQTT 连不上 | Pi IP 不是 192.168.4.1 | 检查 dhcpcd 静态 IP 配置 |
| 抓不到流量 | 设备没连 Pi 热点（连了别的 WiFi） | 确认设备连的是 iot-community |
| 模型误判 | 域偏移 | 按第 7 步校准遥测参数 |
| `/api/device/detect` 返回空 | 缓冲区没有流量 | 先启动抓包、等设备发流量 |
| ONNX 加载失败 | 缺 .onnx.data 文件 | 确认 device_gnn.onnx 和 .data 在同一目录 |

---

## 完成标志（P0 跑通的判据）

1. ✅ 6 台设备连上 Pi 热点，正常发 MQTT 遥测
2. ✅ Pi 抓包 + 设备检测正常，正常设备判"绿"
3. ✅ 触发攻击模式，被感染设备判"红"
4. ✅ 隔离指令生效，设备物理阻断

**达到这 4 条 = P0 检测闭环跑通，可开始现场演示。**

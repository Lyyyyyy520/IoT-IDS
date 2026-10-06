# ESP32 设备固件使用说明

`community_device/community_device.ino` 是智慧社区 IoT 设备模拟固件，每块 ESP32 模拟一种社区设备，通过 MQTT 遥测上报状态，支持"攻击模式"模拟被僵尸网络感染。

## 1. 设备类型与硬件对应

| DEVICE_TYPE | 设备 | 引脚 | 执行器 | 被入侵后的反应 |
|-------------|------|------|--------|---------------|
| `DEVICE_DOOR` | 智能门禁 | GPIO0 | SG90 舵机 | 舵机"开门" |
| `DEVICE_LIGHT` | 智能路灯 | GPIO1 | LED | 灯疯狂闪烁 |
| `DEVICE_PLUG` | 智能插座 | GPIO2 | 继电器 | 继电器反复通断 |
| `DEVICE_SENSOR` | 温湿度传感器 | GPIO3 | DHT11 | 上报假数据 |
| `DEVICE_SPEAKER` | 智能音箱 | GPIO4 | 蜂鸣器 | 发出怪声 |

> 摄像头（ESP32-CAM）是单独设备，用另一套固件（见第 6 节）。

## 2. 编译前安全配置

每个固件目录都包含 `device_secrets.example.h`。烧录前复制为
`device_secrets.h`，并为每台设备设置独立 MQTT 密码；真实 secrets 文件已
被 `.gitignore` 排除，禁止提交到仓库。

```cpp
#define IOT_WIFI_SSID "iot-community"
#define IOT_WIFI_PASSWORD "本地热点强密码"
#define IOT_DEVICE_ID "sensor-01"
#define IOT_MQTT_USERNAME IOT_DEVICE_ID
#define IOT_MQTT_PASSWORD "该设备唯一的 MQTT 密码"
#define IOT_LAB_ATTACK_ENABLED false
```

`IOT_DEVICE_ID` 同时作为 MQTT 用户名。Mosquitto ACL 只允许该用户发布
自己的 `community/{device_id}/status` 和订阅自己的 control 主题。

**6 台设备建议配置：**

| 设备 | DEVICE_TYPE | DEVICE_ID | 静态 IP（树莓派 DHCP 绑定） |
|------|-------------|-----------|---------------------------|
| 摄像头 | ESP32-CAM（另套固件） | camera-01 | 192.168.4.10 |
| 门禁 | `DEVICE_DOOR` | door-01 | 192.168.4.11 |
| 路灯 | `DEVICE_LIGHT` | light-01 | 192.168.4.12 |
| 插座 | `DEVICE_PLUG` | plug-01 | 192.168.4.13 |
| 传感器 | `DEVICE_SENSOR` | sensor-01 | 192.168.4.14 |
| 音箱 | `DEVICE_SPEAKER` | speaker-01 | 192.168.4.15 |

## 3. 依赖库（Arduino IDE 库管理器搜索安装）

| 库 | 用途 | 需要的设备 |
|----|------|-----------|
| PubSubClient (Nick O'Leary) | MQTT | 全部 |
| DHT sensor library (Adafruit) | 温湿度 | SENSOR |
| ESP32Servo (Kevin Harrington) | 舵机 | DOOR |

## 4. 烧录步骤（Arduino IDE）

1. 开发板管理器安装 **ESP32** 支持包（Arduino-ESP32 core）
2. 开发板选择：**ESP32C3 Dev Module**
3. 插 USB，选择对应串口
4. 复制并填写 `device_secrets.h`
5. 改好设备类型配置 → 上传

## 5. MQTT 主题与攻击模式

| 主题 | 方向 | 说明 |
|------|------|------|
| `community/{DEVICE_ID}/status` | 设备→Pi | 遥测上报（每 5s） |
| `community/{DEVICE_ID}/control` | Pi→设备 | 控制指令 |

两套固件现在统一发送 MQTT 心跳 schema v2。每次启动生成新的 32 位十六进制
`boot_id`，同一启动会话内 `sequence` 从 1 开始递增；MAC 和 IP 均从设备运行状态
读取，设备类型字段放在 `telemetry` 对象内。示例：

```json
{
  "schema_version": 2,
  "device_id": "sensor-01",
  "boot_id": "4f8c3d1670f24dc982a4e565e27f7810",
  "sequence": 1,
  "firmware_version": "0.3.0",
  "uptime_ms": 5100,
  "ip": "192.168.4.14",
  "mac": "AA:BB:CC:DD:EE:14",
  "telemetry": {
    "device_type": "sensor",
    "temp": 25.1,
    "humidity": 50.2
  }
}
```

设备 `uptime_ms` 只用于重放和诊断证据，在线状态以服务器实际接收时间为准。
串口日志只打印 boot、sequence 和发布结果，不打印完整 payload 或任何 MQTT 密码。

**控制指令**（发到 control 主题）：

| 指令 | 效果 |
|------|------|
| `attack` | 仅在隔离实验开关启用时，向本地靶机发送受限 UDP 流量 |
| `normal` | 恢复正常模式 |
| `block` | 隔离（物理阻断：锁死/断电/静音） |

攻击实验默认关闭。启用前必须同时确认：目标是 `192.168.4.0/24` 内的本地
靶机、树莓派禁止该流量转发至公网、MQTT ACL 已生效，并准备好断电停止手段。
固件硬限制为约 10 包/秒、最长 30 秒，到时自动恢复正常。

使用具备控制主题权限的独立管理身份发布指令：

```bash
mosquitto_pub -h 192.168.4.1 -u iot-ids-backend -P '<本地凭据>' \
  -t "community/door-01/control" -m "attack"
mosquitto_pub -h 192.168.4.1 -u iot-ids-backend -P '<本地凭据>' \
  -t "community/door-01/control" -m "normal"
```

第一版产品不会自动发布 `block`，只展示人工处置建议；旧固件中的 `block`
分支仅为后续受控实验保留。

## 6. 摄像头（ESP32-CAM）说明

摄像头用独立固件 `camera_device/camera_device.ino`（已写好）：
- 正常：云台缓慢扫描 + MQTT 心跳
- 被入侵：云台疯狂乱转 + UDP 洪水
- 隔离：云台停止

硬件：ESP32-CAM + SG90 舵机（云台，接 GPIO2）。依赖库：PubSubClient + ESP32Servo。

摄像头不做复杂视频流，只需 MQTT 心跳 + 云台动作（Pi 检测的是"这台设备在通信"，不是画面内容）。

## 7. 域偏移提醒

固件生成的流量（MQTT 遥测 + UDP 洪水）与训练数据 CICIoT2023 的流量模式**可能有差异**（域偏移），导致模型现场误判。现场联调时需**校准遥测频率/包长**，使设备流量接近训练分布。见 `dev-logs/2026-08-28.md` 的域偏移记录。
